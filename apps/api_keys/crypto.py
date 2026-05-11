from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

from django.conf import settings


def _pepper_bytes() -> bytes:
    pepper = getattr(settings, "API_KEY_HMAC_PEPPER", "") or settings.SECRET_KEY
    return pepper.encode("utf-8")


def hash_api_key(raw_key: str) -> str:
    """Keyed digest stored in DB. Not a password hash — optimized for high-QPS verification."""
    return hmac.new(_pepper_bytes(), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def timing_safe_equal(expected_hex: str, actual_hex: str) -> bool:
    return hmac.compare_digest(expected_hex, actual_hex)


def generate_public_id() -> str:
    return secrets.token_hex(6)


def generate_secret_component() -> str:
    return secrets.token_hex(32)


def format_raw_api_key(*, public_id: str, secret_component: str) -> str:
    return f"sk_local_{public_id}_{secret_component}"
