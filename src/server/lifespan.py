import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from config.loader import ConfigManager
from webhook.dispatcher import dispatcher_worker
from logger.rotator import log_rotator_worker
from api.federation.sync import sync_federated_servers
from db.pool import DatabasePoolManager
from cache.__init__ import CacheManager
from security.storage import SecurityStorage

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting NexusGate")
    config = ConfigManager.get()
    
    # 1. Start Config Watcher (background task)
    config_task = asyncio.create_task(ConfigManager.watch())
    
    # 1.5 Init Security Database (Bans, Keys, etc.)
    await SecurityStorage.init_db()

    # 1.6 Init Cache Database (if using SQLite backend)
    if config.rate_limit.backend == "sqlite" or config.cache.backend == "sqlite":
        from cache.sqlite_backend import SQLiteCache
        await SQLiteCache.init_db()
    
    # 2. Init DB Pools (done lazily on first request via DatabasePoolManager, but we can log)
    logger.info("Database pools initialized (lazy connecting on demand)")
    
    # 3. Start Webhook Dispatcher
    webhook_task = None
    if config.features.webhook and config.webhooks.enabled:
        webhook_task = asyncio.create_task(dispatcher_worker())
    
    # 4. Start Log Rotator
    rotator_task = asyncio.create_task(log_rotator_worker())
    
    # 5. Start Federation Sync
    fed_task = None
    if config.features.federation and config.federation.enabled:
        fed_task = asyncio.create_task(sync_federated_servers())

    yield

    # Shutdown
    logger.info("Shutting down NexusGate")
    
    # Cancel tasks Gracefully
    config_task.cancel()
    if webhook_task: webhook_task.cancel()
    rotator_task.cancel()
    if fed_task: fed_task.cancel()
    
    await DatabasePoolManager.shutdown()
    
    logger.info("Shutdown complete")
