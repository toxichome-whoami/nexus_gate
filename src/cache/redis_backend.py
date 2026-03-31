import structlog
import asyncio
from typing import Any, Optional
import orjson

try:
    import redis.asyncio as redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    
from config.loader import ConfigManager

logger = structlog.get_logger()

class RedisCache:
    _instance = None
    _client: Optional[redis.Redis] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisCache, cls).__new__(cls)
        return cls._instance
        
    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            if not HAS_REDIS:
                raise RuntimeError("Redis dependency not found. Install nexusgate[redis]")
                
            config = ConfigManager.get()
            url = config.cache.redis_url
            if not url:
                raise ValueError("Redis URL not configured")
                
            cls._client = redis.from_url(url, decode_responses=True)
            logger.info("Initialized redis cache", url=url)
            
        return cls._client
        
    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        client = await cls.get_client()
        val = await client.get(key)
        if val is not None:
            try:
                return orjson.loads(val)
            except Exception:
                return val
        return None
        
    @classmethod
    async def set(cls, key: str, value: Any, ttl: Optional[float] = None) -> None:
        client = await cls.get_client()
        config = ConfigManager.get()
        ttl = int(ttl) if ttl is not None else config.cache.default_ttl
        
        if not isinstance(value, (str, bytes)):
            value = orjson.dumps(value)
            
        await client.setex(key, ttl, value)
        
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
    async def check_rate_limit(cls, limits_key: str, window: int, limit: int, penalty_key: str, burst: int, penalty_cooldown: int) -> tuple[bool, int]:
        """
        Atomic sliding window rate limit check using a Redis Lua script.
        Returns (is_violated, current_count).
        """
        client = await cls.get_client()
        
        # Lua script for sliding window rate limiting.
        # ARGV[1]: window_start (now - window)
        # ARGV[2]: now
        # ARGV[3]: limit + burst
        # ARGV[4]: window (expiration for the sorted set)
        lua_script = """
        local key = KEYS[1]
        local window_start = tonumber(ARGV[1])
        local now = tonumber(ARGV[2])
        local limit_with_burst = tonumber(ARGV[3])
        local window = tonumber(ARGV[4])

        -- Remove timestamps outside the current window
        redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
        
        -- Count remaining timestamps
        local current_count = redis.call('ZCARD', key)

        if current_count >= limit_with_burst then
            return {1, current_count}
        end

        -- Add current timestamp
        redis.call('ZADD', key, now, now)
        -- Set expiration for the set
        redis.call('EXPIRE', key, window)
        
        return {0, current_count + 1}
        """
        
        now = time.time()
        window_start = now - window
        
        # 1. Check if IP is already penalized
        is_penalized = await client.get(penalty_key)
        if is_penalized:
            return True, limit + 1
            
        # 2. Run Lua script for atomic sliding window
        try:
            violated, count = await client.eval(lua_script, 1, limits_key, window_start, now, limit + burst, window)
            
            if violated == 1:
                # Handle violation tracking and potential penalty
                violation_key = f"rl:violations:{limits_key.split(':')[-1]}" # Assuming format rl:ip:X...
                # We can't easily parse the key here without assumptions, so we'll use the whole limits_key for violation tracking if needed, 
                # but better to pass the identifier explicitly if we want clean keys.
                # For now, let's just use a simple approach.
                
                # Increment violation counter
                violations = await client.incr(violation_key)
                await client.expire(violation_key, window * 2)
                
                if violations >= 10:
                    await client.setex(penalty_key, penalty_cooldown, "1")
                    logger.warning("Applied IP penalty in Redis", key=penalty_key)
                
                return True, count
            
            return False, count
        except Exception as e:
            logger.error("Redis rate limit LUA execution failed", error=str(e))
            return False, 0
import time
