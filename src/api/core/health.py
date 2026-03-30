import time
import os
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psutil

from config.loader import ConfigManager
from api.responses import success_response

router = APIRouter()
uptime_start = time.time()

@router.get("/")
async def root(request: Request):
    """Server status."""
    config = ConfigManager.get()

    data = {
        "status": "online",
        "name": "NexusGate",
        "version": __version__,
        "uptime_seconds": int(time.time() - uptime_start),
        "features": config.features.model_dump()
    }

    return success_response(request, data)

@router.get("/ready")
async def ready(request: Request):
    """Simple readiness probe."""
    return {"ready": True}

@router.get("/health")
async def health(request: Request):
    """Detailed health of subsystems."""
    config = ConfigManager.get()

    # Ping DB Pools
    from db.pool import DatabasePoolManager
    db_status = {}
    all_dbs_up = True
    for alias in config.database:
        engine = await DatabasePoolManager.get_engine(alias)
        is_up = await engine.health_check() if engine else False
        db_status[alias] = "up" if is_up else "down"
        if not is_up: all_dbs_up = False

    # Ping Caches
    from cache.__init__ import CacheManager
    from cache.memory import MemoryCache
    from cache.redis_backend import RedisCache

    cache_status = {"enabled": config.cache.enabled}
    if config.cache.enabled:
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

    # Check Storages
    storage_status = {}
    for alias, sc in config.storage.items():
        if os.path.exists(sc.path):
            st = os.statvfs(sc.path) if hasattr(os, 'statvfs') else None
            free_bytes = (st.f_bavail * st.f_frsize) if st else 0
            storage_status[alias] = {"status": "up", "free_space_bytes": free_bytes}
        else:
            storage_status[alias] = {"status": "down"}

    data = {
        "status": "healthy" if all_dbs_up else "degraded",
        "checks": {
            "server": {"status": "up"},
            "databases": db_status,
            "storages": storage_status,
            "cache": cache_status,
            "federation": {} # Federation sync writes to FederationState in background
        },
        "system": {
            "memory_used_mb": int(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024),
            "cpu_percent": psutil.Process(os.getpid()).cpu_percent(),
        }
    }

    return success_response(request, data)
