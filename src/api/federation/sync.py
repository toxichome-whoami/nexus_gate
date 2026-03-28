import asyncio
import httpx
import structlog
from typing import Dict, Any

from config.loader import ConfigManager
from cache.memory import MemoryCache # For simplicity, store fed state in memory

logger = structlog.get_logger()

class FederationState:
    _instance = None
    # alias -> status
    servers: Dict[str, Dict[str, Any]] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FederationState, cls).__new__(cls)
        return cls._instance

async def sync_federated_servers():
    """Background task to poll federated servers for health and capabilities."""
    logger.info("Federation sync started")
    config = ConfigManager.get()
    
    if not config.features.federation or not config.federation.enabled:
        logger.info("Federation is disabled, shutting down sync task")
        return
        
    state = FederationState()
    interval = config.federation.sync_interval
    
    async with httpx.AsyncClient() as client:
        while True:
            try:
                for alias, srv_config in config.federation.server.items():
                    url = srv_config.url.rstrip("/")
                    verify = srv_config.trust_mode == "verify"
                    headers = {
                        "Authorization": f"Bearer {srv_config.api_key}"
                    }
                    
                    try:
                        # Ping health
                        resp = await client.get(f"{url}/health", headers=headers, verify=verify, timeout=5)
                        resp.raise_for_status()
                        health_data = resp.json()
                        
                        # Just storing basic info for now
                        state.servers[alias] = {
                            "status": "up",
                            "latency_ms": health_data.get("meta", {}).get("duration_ms", 0),
                            "databases": health_data.get("data", {}).get("checks", {}).get("databases", {}),
                            "storages": health_data.get("data", {}).get("checks", {}).get("storages", {})
                        }
                        
                    except httpx.HTTPError as e:
                        logger.warning("Failed to sync with federated server", alias=alias, error=str(e))
                        state.servers[alias] = {
                            "status": "down",
                            "error": str(e)
                        }
                        
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("Federation sync shutting down")
                break
            except Exception as e:
                logger.error("Federation sync error", error=str(e))
                await asyncio.sleep(interval)
