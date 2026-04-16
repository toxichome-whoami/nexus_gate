import time
import os
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
import psutil

from config.loader import ConfigManager
from api.responses import success_response
from db.pool import DatabasePoolManager
from cache import CacheManager
from cache.memory import MemoryCache
from cache.redis_backend import RedisCache

router = APIRouter()
uptime_start = time.time()

# ─────────────────────────────────────────────────────────────────────────────
# System Subroutines
# ─────────────────────────────────────────────────────────────────────────────

async def _evaluate_database_health(config) -> tuple[dict, bool]:
    """Generates execution masks validating absolute pool availability maps."""
    db_status = {}
    all_dbs_up = True
    
    for alias in config.database:
        engine = await DatabasePoolManager.get_engine(alias)
        is_up = await engine.health_check() if engine else False
        db_status[alias] = "up" if is_up else "down"
        if not is_up: 
            all_dbs_up = False
            
    return db_status, all_dbs_up

async def _evaluate_cache_health(config) -> dict:
    """Verifies Redis TCP pings explicitly avoiding network suspension errors."""
    cache_status = {"enabled": config.cache.enabled}
    if not config.cache.enabled:
        return cache_status
        
    cache_status["backend"] = config.cache.backend
    if config.cache.backend == "redis":
        try:
            client = await RedisCache.get_client()
            await client.ping()
            cache_status["status"] = "up"
        except Exception:
            cache_status["status"] = "down"
    else:
        cache_status.update(MemoryCache.stats())
        
    return cache_status

def _evaluate_storage_health(config) -> dict:
    """Validates physical OS mount states generating storage constraints locally."""
    storage_status = {}
    for alias, storage_cfg in config.storage.items():
        if os.path.exists(storage_cfg.path):
            stat_vfs = os.statvfs(storage_cfg.path) if hasattr(os, 'statvfs') else None
            free_bytes = (stat_vfs.f_bavail * stat_vfs.f_frsize) if stat_vfs else 0
            storage_status[alias] = {"status": "up", "free_space_bytes": free_bytes}
        else:
            storage_status[alias] = {"status": "down"}
            
    return storage_status

# ─────────────────────────────────────────────────────────────────────────────
# Exposed Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/")
async def root(request: Request):
    """Base application heartbeat."""
    config = ConfigManager.get()
    return success_response(request, {
        "status": "online",
        "name": "NexusGate",
        "version": "1.0.0",
        "uptime_seconds": int(time.time() - uptime_start),
        "features": config.features.model_dump()
    })

@router.get("/ready")
async def ready(request: Request):
    """External container load balancer latch testing probe."""
    return {"ready": True}

@router.get("/health")
async def health(request: Request):
    """Detailed synchronous orchestration of all underlying infrastructure states."""
    config = ConfigManager.get()

    db_status, all_dbs_up = await _evaluate_database_health(config)
    cache_status = await _evaluate_cache_health(config)
    storage_status = _evaluate_storage_health(config)

    system_stats = {
        "memory_used_mb": int(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024),
        "cpu_percent": psutil.Process(os.getpid()).cpu_percent(),
    }

    return success_response(request, {
        "status": "healthy" if all_dbs_up else "degraded",
        "checks": {
            "server": {"status": "up"},
            "databases": db_status,
            "storages": storage_status,
            "cache": cache_status,
            "federation": {} 
        },
        "system": system_stats
    })
