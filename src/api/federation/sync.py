import asyncio
import base64
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
    
    while True:
        try:
            for alias, srv_config in config.federation.server.items():
                url = srv_config.url.rstrip("/")
                verify_ssl = srv_config.trust_mode == "verify"
                encoded_secret = base64.b64encode(srv_config.secret.encode("utf-8")).decode("utf-8")
                headers = {
                    "X-Federation-Secret": encoded_secret,
                    "X-Federation-Node": srv_config.node_id,
                }
                
                try:
                    async with httpx.AsyncClient(verify=verify_ssl, timeout=10) as client:
                        # 1. Health check
                        resp = await client.get(f"{url}/health", headers=headers)
                        resp.raise_for_status()
                        health_data = resp.json()
                        
                        latency = health_data.get("meta", {}).get("duration_ms", 0)
                        health_dbs = health_data.get("data", {}).get("checks", {}).get("databases", {})
                        health_storages = health_data.get("data", {}).get("checks", {}).get("storages", {})

                        # 2. Fetch real database metadata
                        databases = {}
                        try:
                            db_resp = await client.get(f"{url}/api/db/databases", headers=headers)
                            if db_resp.status_code == 200:
                                db_data = db_resp.json()
                                for db_info in db_data.get("data", {}).get("databases", []):
                                    db_name = db_info.get("name", "")
                                    databases[db_name] = {
                                        "status": health_dbs.get(db_name, "unknown"),
                                        "engine": db_info.get("engine", "unknown"),
                                        "mode": db_info.get("mode", "unknown"),
                                        "tables_count": db_info.get("tables_count", 0),
                                    }
                            else:
                                # Non-200 (e.g. 401) — fallback to health data
                                for db_name, db_status in health_dbs.items():
                                    databases[db_name] = {
                                        "status": db_status,
                                        "engine": "unknown",
                                        "mode": "unknown",
                                        "tables_count": 0,
                                    }
                        except Exception:
                            # Exception — fallback to health data
                            for db_name, db_status in health_dbs.items():
                                databases[db_name] = {
                                    "status": db_status,
                                    "engine": "unknown",
                                    "mode": "unknown",
                                    "tables_count": 0,
                                }

                    state.servers[alias] = {
                        "status": "up",
                        "latency_ms": latency,
                        "databases": databases,
                        "storages": health_storages,
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


