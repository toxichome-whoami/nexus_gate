import asyncio
from typing import Any, Optional
import structlog
from cachetools import TTLCache

from config.loader import ConfigManager
from utils.size_parser import parse_size

logger = structlog.get_logger()

class MemoryCache:
    _instance = None
    _cache: Optional[TTLCache] = None
    _lock: Optional[asyncio.Lock] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MemoryCache, cls).__new__(cls)
        return cls._instance
        
    @classmethod
    def get_cache(cls) -> TTLCache:
        if cls._cache is None:
            config = ConfigManager.get()
            
            # Simple heuristic for bounded cache
            # Use maxsize representing item count roughly derived from memory bound
            # Very basic assumption: 10KB per object on average
            max_memory_bytes = parse_size(config.cache.max_memory)
            max_items = max(100, max_memory_bytes // 10240)
            
            cls._cache = TTLCache(maxsize=max_items, ttl=config.cache.default_ttl)
            cls._lock = asyncio.Lock()
            logger.info("Initialized memory cache", max_items=max_items, default_ttl=config.cache.default_ttl)
            
        return cls._cache
        
    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        cache = cls.get_cache()
        async with cls._lock:
            return cache.get(key)
            
    @classmethod
    async def set(cls, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Sets a value in the cache. Note TTLCache from cachetools doesn't 
        trivially support per-object TTL natively without subclassing, so we 
        fallback to default TTL for this simplified memory backend. """
        cache = cls.get_cache()
        async with cls._lock:
            # For simplicity, memory cache ignores per-item TTL differences
            cache[key] = value
            
    @classmethod
    async def delete(cls, key: str) -> bool:
        cache = cls.get_cache()
        async with cls._lock:
            if key in cache:
                del cache[key]
                return True
        return False
        
    @classmethod
    async def flush(cls) -> None:
        cache = cls.get_cache()
        async with cls._lock:
            cache.clear()
            
    @classmethod
    def stats(cls) -> dict:
        cache = cls.get_cache()
        return {
            "status": "up",
            "backend": "memory",
            "size_items": cache.currsize,
            "max_items": cache.maxsize,
            "hits": cache.hits,
            "misses": cache.misses
        }
