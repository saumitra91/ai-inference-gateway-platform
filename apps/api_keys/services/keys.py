from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from apps.api_keys.crypto import format_raw_api_key, generate_public_id, generate_secret_component, hash_api_key
from apps.api_keys.models import APIKey
from apps.api_keys.parsing import build_full_raw_key, parse_api_key
from apps.api_keys.services.audit import write_audit

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CreatedAPIKey:
    raw_key: str
    api_key: APIKey


def create_api_key(
    *,
    owner: AbstractBaseUser,
    actor: AbstractBaseUser,
    label: str = "",
    expires_at: datetime | None = None,
    rate_limit_rpm: int = 120,
) -> CreatedAPIKey:
    """Create a key and return the raw token exactly once."""
    for _ in range(5):
        public_id = generate_public_id()
        if not APIKey.objects.filter(public_id=public_id).exists():
            break
    else:
        raise RuntimeError("Could not allocate a unique API key public id")

    secret_component = generate_secret_component()
    raw = format_raw_api_key(public_id=public_id, secret_component=secret_component)

    digest = hash_api_key(raw)
    with transaction.atomic():
        key = APIKey.objects.create(
            user=owner,
            public_id=public_id,
            secret_hash=digest,
            label=label,
            expires_at=expires_at,
            rate_limit_rpm=rate_limit_rpm,
        )
        write_audit(action="api_key.created", actor=actor, api_key=key, message=label)

    logger.info("api_key_created", extra={"api_key_public_id": public_id, "owner_id": owner.pk})
    return CreatedAPIKey(raw_key=raw, api_key=key)


def revoke_api_key(*, key: APIKey, actor: AbstractBaseUser | None, reason: str = "") -> None:
    if key.revoked_at is not None:
        return
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])
    write_audit(action="api_key.revoked", actor=actor, api_key=key, message=reason)
    logger.warning("api_key_revoked", extra={"api_key_public_id": key.public_id, "actor_id": getattr(actor, "pk", None)})


def delete_api_key(*, key: APIKey, actor: AbstractBaseUser | None) -> dict[str, Any]:
    public_id = key.public_id
    pk = key.pk
    key.delete()
    write_audit(action="api_key.deleted", actor=actor, api_key=None, api_key_public_id=public_id, message=str(pk))
    logger.warning("api_key_deleted", extra={"api_key_public_id": public_id, "actor_id": getattr(actor, "pk", None)})
    return {"deleted": True, "public_id": public_id}
