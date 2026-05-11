from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.utils import timezone

from apps.api_keys.crypto import hash_api_key, timing_safe_equal
from apps.api_keys.models import APIKey
from apps.api_keys.parsing import parse_api_key

logger = logging.getLogger(__name__)

_BEARER_PREFIX: Final[str] = "Bearer "


@dataclass(frozen=True, slots=True)
class ResolvedAPIKey:
    api_key: APIKey


class APIKeyAuthError(Exception):
    def __init__(self, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _extract_bearer_token(request: HttpRequest) -> str | None:
    auth = request.headers.get("Authorization") or request.META.get("HTTP_AUTHORIZATION") or ""
    auth = auth.strip()
    if not auth.startswith(_BEARER_PREFIX):
        return None
    return auth[len(_BEARER_PREFIX) :].strip()


@sync_to_async(thread_sensitive=True)
def _load_active_key(public_id: str) -> APIKey | None:
    return (
        APIKey.objects.select_related("user")
        .filter(public_id=public_id, revoked_at__isnull=True)
        .first()
    )


async def resolve_bearer_api_key(request: HttpRequest) -> ResolvedAPIKey:
    token = _extract_bearer_token(request)
    if not token:
        raise APIKeyAuthError("Missing bearer token", status=401)

    parsed = parse_api_key(token)
    if parsed is None:
        raise APIKeyAuthError("Malformed API key", status=401)

    key = await _load_active_key(parsed.public_id)
    if key is None:
        # Do not leak whether public id exists — still perform a compare_digest on equal-length digests.
        timing_safe_equal(hash_api_key("sk_local_invalid0_invalid0"), hash_api_key(token))
        raise APIKeyAuthError("Invalid API key", status=401)

    now = timezone.now()
    if key.expires_at is not None and key.expires_at <= now:
        raise APIKeyAuthError("API key expired", status=401)

    expected = hash_api_key(token)
    if not timing_safe_equal(key.secret_hash, expected):
        raise APIKeyAuthError("Invalid API key", status=401)

    return ResolvedAPIKey(api_key=key)


def resolve_bearer_api_key_sync(request: HttpRequest) -> ResolvedAPIKey:
    """Sync variant for middleware / synchronous views."""
    token = _extract_bearer_token(request)
    if not token:
        raise APIKeyAuthError("Missing bearer token", status=401)

    parsed = parse_api_key(token)
    if parsed is None:
        raise APIKeyAuthError("Malformed API key", status=401)

    key = APIKey.objects.select_related("user").filter(public_id=parsed.public_id, revoked_at__isnull=True).first()
    if key is None:
        timing_safe_equal(hash_api_key("sk_local_invalid0_invalid0"), hash_api_key(token))
        raise APIKeyAuthError("Invalid API key", status=401)

    now = timezone.now()
    if key.expires_at is not None and key.expires_at <= now:
        raise APIKeyAuthError("API key expired", status=401)

    expected = hash_api_key(token)
    if not timing_safe_equal(key.secret_hash, expected):
        raise APIKeyAuthError("Invalid API key", status=401)

    return ResolvedAPIKey(api_key=key)


def mask_authorization_header(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith(_BEARER_PREFIX):
        return "Bearer [REDACTED]"
    return "[REDACTED]"
