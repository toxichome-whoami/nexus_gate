import structlog
from typing import Any, Optional

from config.loader import ConfigManager
from .memory import MemoryCache
from .redis_backend import RedisCache

logger = structlog.get_logger()

class CacheManager:
    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        config = ConfigManager.get()
        if not config.cache.enabled:
            return None
            
        if config.cache.backend == "redis":
            return await RedisCache.get(key)
        else:
            return await MemoryCache.get(key)

    @classmethod
    async def set(cls, key: str, value: Any, ttl: Optional[float] = None) -> None:
        config = ConfigManager.get()
        if not config.cache.enabled:
            return
            
        if config.cache.backend == "redis":
            await RedisCache.set(key, value, ttl)
        else:
            await MemoryCache.set(key, value, ttl)

    @classmethod
    async def delete(cls, key: str) -> bool:
        config = ConfigManager.get()
        if not config.cache.enabled:
            return False
            
        if config.cache.backend == "redis":
            return await RedisCache.delete(key)
        else:
            return await MemoryCache.delete(key)
