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
            "hits": getattr(cache, "hits", 0),
            "misses": getattr(cache, "misses", 0)
        }

    @classmethod
    async def check_rate_limit(cls, limits_key: str, window: int, limit: int, penalty_key: str, burst: int, penalty_cooldown: int) -> tuple[bool, int]:
        """
        In-memory sliding window rate limit.
        Since it's in a single process, the internal lock is sufficient.
        """
        now = time.time()
        window_start = now - window
        cache = cls.get_cache()
        
        async with cls._lock:
            # 1. Check penalty
            if penalty_key in cache:
                return True, limit + 1
            
            # 2. Manage history
            history = cache.get(limits_key, [])
            history = [ts for ts in history if ts > window_start]
            
            if len(history) >= limit + burst:
                # Violation!
                v_key = f"rl:violations:{limits_key}"
                violations = cache.get(v_key, 0) + 1
                cache[v_key] = violations
                # Note: per-item TTL isn't fully supported in cachetools.TTLCache 
                # but it uses default_ttl. Good enough for memory-only mode.
                
                if violations >= 10:
                    cache[penalty_key] = True
                    logger.warning("Applied IP penalty in memory", key=penalty_key)
                
                return True, len(history)
            
            # 3. Success
            history.append(now)
            cache[limits_key] = history
            return False, len(history)

import time
