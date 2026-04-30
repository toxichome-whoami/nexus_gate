import time
from typing import Any, Optional

import orjson
import structlog

try:
    import redis.asyncio as redis

    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    redis: Any = None

from config.loader import ConfigManager

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Lua Script Definitions
# ─────────────────────────────────────────────────────────────────────────────

# ARGV[1]: window_start (now - window)
# ARGV[2]: now
# ARGV[3]: limit + burst
# ARGV[4]: window (expiration for the sorted set)
SLIDING_WINDOW_LUA_SCRIPT = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local limit_with_burst = tonumber(ARGV[3])
local window = tonumber(ARGV[4])

redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
local current_count = redis.call('ZCARD', key)

if current_count >= limit_with_burst then
    return {1, current_count}
end

redis.call('ZADD', key, now, now)
redis.call('EXPIRE', key, window)

return {0, current_count + 1}
"""

# ─────────────────────────────────────────────────────────────────────────────
# Internal Subsystems
# ─────────────────────────────────────────────────────────────────────────────


async def _handle_penalty_application(
    client: Any, limits_key: str, penalty_key: str, window: int, penalty_cooldown: int
) -> None:
    """Manages atomic incrementation and expiration configurations for rate limit penalties."""
    violation_key = f"rl:violations:{limits_key.split(':')[-1]}"

    violations = await client.incr(violation_key)
    await client.expire(violation_key, window * 2)

    if violations >= 10:
        await client.setex(penalty_key, penalty_cooldown, "1")
        logger.warning("Applied IP penalty executing in Redis", key=penalty_key)


# ─────────────────────────────────────────────────────────────────────────────
# Core Adapter
# ─────────────────────────────────────────────────────────────────────────────


class RedisCache:
    """Massive capacity production caching adapter integrating asynchronous Redis."""

    _instance = None
    _client: Optional[Any] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisCache, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def get_client(cls) -> Any:
        if cls._client is None:
            if not HAS_REDIS:
                raise RuntimeError(
                    "Redis dependency not found. Install nexusgate[redis]"
                )
            config = ConfigManager.get()
            url = config.cache.redis_url
            if not url:
                raise ValueError("Redis URL not configured")

            cls._client = redis.from_url(url, decode_responses=True)
            logger.info("Initialized redis cache pool", url=url)

        return cls._client

    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        client = await cls.get_client()
        val = await client.get(key)

        if val is None:
            return None

        try:
            return orjson.loads(val)
        except Exception:
            return val

    @classmethod
    async def set(cls, key: str, value: Any, ttl: Optional[float] = None) -> None:
        client = await cls.get_client()
        config = ConfigManager.get()
        expiration_ttl = int(ttl) if ttl is not None else config.cache.default_ttl

        if not isinstance(value, (str, bytes)):
            value = orjson.dumps(value)

        await client.setex(key, expiration_ttl, value)

    @classmethod
    async def delete(cls, key: str) -> bool:
        client = await cls.get_client()
        return bool(await client.delete(key))

    @classmethod
    async def flush(cls) -> None:
        client = await cls.get_client()
        await client.flushdb()

    @classmethod
    async def shutdown(cls) -> None:
        if cls._client:
            await cls._client.close()
            cls._client = None

    @classmethod
    async def check_rate_limit(
        cls,
        limits_key: str,
        window: int,
        limit: int,
        penalty_key: str,
        burst: int,
        penalty_cooldown: int,
    ) -> tuple[bool, int]:
        """Atomically evaluates sliding limits executing single-instance LUA scripts."""
        client = await cls.get_client()

        is_penalized = await client.get(penalty_key)
        if is_penalized:
            return True, limit + 1

        now = time.time()
        window_start = now - window

        try:
            violated, count = await client.eval(
                SLIDING_WINDOW_LUA_SCRIPT,
                1,
                limits_key,
                window_start,
                now,
                limit + burst,
                window,
            )

            if violated == 1:
                await _handle_penalty_application(
                    client, limits_key, penalty_key, window, penalty_cooldown
                )
                return True, count

            return False, count

        except Exception as script_error:
            logger.error(
                "Redis rate limit LUA execution failed", error=str(script_error)
            )
            return False, 0
