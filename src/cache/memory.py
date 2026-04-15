import time
import asyncio
from typing import Any, Optional
import structlog
from cachetools import TTLCache

from config.loader import ConfigManager
from utils.size_parser import parse_size

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Rate Limit Execution Engine
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_cache_capacity_heuristic(config) -> int:
    """Estimates safe TTLCache sizing limits derived from string representations of Bytes."""
    max_memory_bytes = parse_size(config.cache.max_memory)
    return max(100, max_memory_bytes // 10240)  # Roughly assumes 10KB/object

def _apply_penalty_violation(cache: TTLCache, limits_key: str, penalty_key: str) -> None:
    """Tracks sequential IP lockouts, generating hard penalties when breached."""
    violation_tracker_key = f"rl:violations:{limits_key}"
    violations = cache.get(violation_tracker_key, 0) + 1
    cache[violation_tracker_key] = violations
    
    if violations >= 10:
        cache[penalty_key] = True
        logger.warning("Applied IP penalty in memory boundary", key=penalty_key)

# ─────────────────────────────────────────────────────────────────────────────
# Memory Adapter
# ─────────────────────────────────────────────────────────────────────────────

class MemoryCache:
    """Ultra-fast LRU/TTL bounded runtime mapping used when Redis/SQLite are absent."""
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
            max_items = _resolve_cache_capacity_heuristic(config)
            
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
        cache = cls.get_cache()
        async with cls._lock:
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
        """Atomically evaluates sliding limits executing single-thread async locks."""
        now = time.time()
        window_start = now - window
        cache = cls.get_cache()
        
        async with cls._lock:
            if penalty_key in cache:
                return True, limit + 1
            
            history = cache.get(limits_key, [])
            history = [ts for ts in history if ts > window_start]
            
            if len(history) >= limit + burst:
                _apply_penalty_violation(cache, limits_key, penalty_key)
                return True, len(history)
            
            history.append(now)
            cache[limits_key] = history
            return False, len(history)
