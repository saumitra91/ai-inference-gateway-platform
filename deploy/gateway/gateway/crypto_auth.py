from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

import asyncpg

_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^sk_local_([a-f0-9]{12})_([a-f0-9]{64})$")


@dataclass(frozen=True, slots=True)
class ParsedAPIKey:
    public_id: str
    raw_token: str


def parse_api_key(raw: str) -> ParsedAPIKey | None:
    m = _KEY_RE.match(raw.strip())
    if not m:
        return None
    return ParsedAPIKey(public_id=m.group(1), raw_token=raw.strip())


def hash_api_key_hex(raw_key: str, *, pepper: str, secret_key: str) -> str:
    p = (pepper or secret_key).encode("utf-8")
    return hmac.new(p, raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def timing_safe_equal_hex(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


@dataclass(frozen=True, slots=True)
class APIKeyContext:
    id: UUID
    user_id: int
    rate_limit_rpm: int


async def verify_bearer_token(
    *,
    conn: asyncpg.Connection,
    token: str,
    settings_pepper: str,
    settings_secret: str,
) -> APIKeyContext | None:
    parsed = parse_api_key(token)
    if parsed is None:
        return None

    row = await conn.fetchrow(
        """
        SELECT id, user_id, rate_limit_rpm, secret_hash, expires_at, revoked_at
        FROM api_keys_apikey
        WHERE public_id = $1
        """,
        parsed.public_id,
    )
    if row is None:
        timing_safe_equal_hex(
            hash_api_key_hex("sk_local_invalid0_invalid0", pepper=settings_pepper, secret_key=settings_secret),
            hash_api_key_hex(parsed.raw_token, pepper=settings_pepper, secret_key=settings_secret),
        )
        return None

    if row["revoked_at"] is not None:
        return None

    exp = row["expires_at"]
    if exp is not None:
        from datetime import datetime, timezone

        if exp <= datetime.now(timezone.utc):
            return None

    expected_hex: str = row["secret_hash"]
    actual_hex = hash_api_key_hex(parsed.raw_token, pepper=settings_pepper, secret_key=settings_secret)
    if not timing_safe_equal_hex(expected_hex, actual_hex):
        return None

    return APIKeyContext(id=row["id"], user_id=row["user_id"], rate_limit_rpm=int(row["rate_limit_rpm"] or 0))


async def touch_api_key_used(conn: asyncpg.Connection, key_id: UUID) -> None:
    from datetime import datetime, timezone

    await conn.execute(
        """
        UPDATE api_keys_apikey
        SET last_used_at = $2,
            requests_count = requests_count + 1
        WHERE id = $1
        """,
        key_id,
        datetime.now(timezone.utc),
    )
