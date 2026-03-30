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
