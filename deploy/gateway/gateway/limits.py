from __future__ import annotations

import time
from uuid import UUID

from redis.asyncio import Redis


async def consume_rate_limit(*, redis: Redis, api_key_id: UUID, rpm: int) -> bool:
    if rpm <= 0:
        return True
    window = int(time.time() // 60)
    key = f"rl:rpm:{api_key_id}:{window}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 120)
    return n <= rpm
