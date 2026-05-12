from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser
from django.core.cache import cache
from datetime import timezone as dt_timezone

from django.utils import timezone

from apps.users.models import UserProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool


def consume_rate_limit(*, api_key: APIKey) -> RateLimitResult:
    """Fixed-window per-minute counter in Redis/LocMem."""
    if api_key.rate_limit_rpm <= 0:
        return RateLimitResult(allowed=True)

    window = int(timezone.now().timestamp() // 60)
    cache_key = f"rl:rpm:{api_key.pk}:{window}"
    try:
        n = int(cache.incr(cache_key))
    except ValueError:
        cache.add(cache_key, 1, timeout=120)
        n = 1

    allowed = n <= int(api_key.rate_limit_rpm)
    if not allowed:
        logger.info("rate_limit_hit", extra={"api_key_id": str(api_key.pk), "n": n})
    return RateLimitResult(allowed=allowed)


def _utc_day_key(now: timezone.datetime) -> str:
    return now.astimezone(dt_timezone.utc).date().isoformat()


def _seconds_until_next_utc_day(now: timezone.datetime) -> int:
    utc_now = now.astimezone(dt_timezone.utc)
    nxt = (utc_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((nxt - utc_now).total_seconds()))


@dataclass(frozen=True, slots=True)
class QuotaResult:
    allowed: bool
    reason: str = ""


def _profile_for(user: AbstractBaseUser) -> UserProfile | None:
    return UserProfile.objects.filter(user=user).first()


def check_user_daily_quota(
    *,
    user: AbstractBaseUser,
    prompt_tokens: int,
    completion_tokens: int,
) -> QuotaResult:
    """Pre-flight checks (UTC day buckets). Does not mutate counters."""
    profile = _profile_for(user)
    if profile is None:
        return QuotaResult(allowed=True)

    now = timezone.now()
    day = _utc_day_key(now)
    ttl = _seconds_until_next_utc_day(now)

    if profile.daily_request_limit is not None:
        rk = f"quota:req:{user.pk}:{day}"
        used_req = int(cache.get(rk, 0))
        if used_req >= int(profile.daily_request_limit):
            return QuotaResult(allowed=False, reason="daily_request_limit_exceeded")

    if profile.daily_token_limit is not None:
        tk = f"quota:tok:{user.pk}:{day}"
        used_tok = int(cache.get(tk, 0))
        add = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
        if used_tok + add > int(profile.daily_token_limit):
            return QuotaResult(allowed=False, reason="daily_token_limit_exceeded")

    _ = ttl  # reserved for future CAS windows
    return QuotaResult(allowed=True)


def record_user_quota_success(
    *,
    user: AbstractBaseUser,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Increment daily quota counters after a successful inference."""
    profile = _profile_for(user)
    if profile is None:
        return

    now = timezone.now()
    day = _utc_day_key(now)
    ttl = _seconds_until_next_utc_day(now)

    if profile.daily_request_limit is not None:
        rk = f"quota:req:{user.pk}:{day}"
        try:
            cache.incr(rk)
        except ValueError:
            cache.add(rk, 1, timeout=ttl)

    if profile.daily_token_limit is not None:
        tk = f"quota:tok:{user.pk}:{day}"
        add = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
        cur = int(cache.get(tk, 0))
        cache.set(tk, cur + add, timeout=ttl)
