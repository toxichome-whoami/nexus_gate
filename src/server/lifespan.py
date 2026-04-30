import asyncio
from contextlib import asynccontextmanager
from typing import List

import structlog
from fastapi import FastAPI

from api.federation.sync import sync_federated_servers
from config.loader import ConfigManager
from db.pool import DatabasePoolManager
from logger.rotator import log_rotator_worker
from security.storage import SecurityStorage
from webhook.dispatcher import dispatcher_worker

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _init_storage_backends(config):
    """Initializes persistent databases required for startup caching and security."""
    await SecurityStorage.init_db()

    # Conditionally boot SQLite cache backend if declared in config
    if config.rate_limit.backend == "sqlite" or config.cache.backend == "sqlite":
        from cache.sqlite_backend import SQLiteCache

        await SQLiteCache.init_db()


def _start_background_daemons(config) -> List[asyncio.Task]:
    """Launches non-blocking background workers based on active feature flags."""
    tasks = []

    # System core functionality
    tasks.append(asyncio.create_task(ConfigManager.watch()))
    tasks.append(asyncio.create_task(log_rotator_worker()))

    # Conditional feature workers
    if config.features.webhook and config.webhooks.enabled:
        tasks.append(asyncio.create_task(dispatcher_worker()))

    if config.features.federation and config.federation.enabled:
        tasks.append(asyncio.create_task(sync_federated_servers()))

    return tasks


async def _stop_background_daemons(tasks: List[asyncio.Task]):
    """Gracefully kills all active background coroutines."""
    for task in tasks:
        task.cancel()
    # Await cancellation completion to prevent memory leaks if required
    # await asyncio.gather(*tasks, return_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# Primary Context
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Controls application bootstrap and teardown sequences dynamically."""
    logger.info("Starting NexusGate bootstrap sequence")
    config = ConfigManager.get()

    # 1. Boot Subsystems
    await _init_storage_backends(config)
    logger.info("Database pools ready (lazy connecting on demand)")

    # 2. Launch Daemons
    active_daemons = _start_background_daemons(config)

    # Yield control to the ASGI server
    yield

    # 3. Teardown Subsystems
    logger.info("Initiating NexusGate shutdown sequence")
    await _stop_background_daemons(active_daemons)
    await DatabasePoolManager.shutdown()

    # 4. Teardown HTTP Clients
    if hasattr(app.state, "http_clients"):
        logger.info("Closing internal HTTP connection pools")
        for client in app.state.http_clients.values():
            await client.aclose()

    logger.info("Shutdown sequence fully completed")
