import asyncio
import base64
import httpx
import structlog
from typing import Dict, Any

from config.loader import ConfigManager

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# State Persistor
# ─────────────────────────────────────────────────────────────────────────────

class FederationState:
    _instance = None
    servers: Dict[str, Dict[str, Any]] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FederationState, cls).__new__(cls)
        return cls._instance

# ─────────────────────────────────────────────────────────────────────────────
# Network Execution Modules
# ─────────────────────────────────────────────────────────────────────────────

async def _poll_single_remote_node(alias: str, srv_config, state: FederationState) -> None:
    """Executes atomic health ping directly targeting alias sockets."""
    url = srv_config.url.rstrip("/")
    verify_ssl = srv_config.trust_mode == "verify"
    encoded_secret = base64.b64encode(srv_config.secret.encode("utf-8")).decode("utf-8")
    
    headers = {
        "X-Federation-Secret": encoded_secret,
        "X-Federation-Node": srv_config.node_id,
    }
    
    try:
        async with httpx.AsyncClient(verify=verify_ssl, timeout=10) as client:
            resp = await client.get(f"{url}/health", headers=headers)
            resp.raise_for_status()
            
            health_data = resp.json()
            latency = health_data.get("meta", {}).get("duration_ms", 0)
            checks_map = health_data.get("data", {}).get("checks", {})

        state.servers[alias] = {
            "status": "up",
            "latency_ms": latency,
            "databases": checks_map.get("databases", {}),
            "storages": checks_map.get("storages", {}),
        }
    except httpx.HTTPError as net_error:
        logger.warning("Failed to sync with federated server", alias=alias, error=str(net_error))
        state.servers[alias] = {"status": "down", "error": str(net_error)}

async def _execute_synchronization_cycle(config: ConfigManager, state: FederationState) -> None:
    """Generates batched poll limits traversing configured aliases."""
    for alias, srv_config in config.federation.server.items():
        await _poll_single_remote_node(alias, srv_config, state)

# ─────────────────────────────────────────────────────────────────────────────
# Primary Daemon Task
# ─────────────────────────────────────────────────────────────────────────────

async def sync_federated_servers():
    """Background task polling federated servers securely ensuring network synchronization."""
    logger.info("Federation sync started")
    config = ConfigManager.get()
    
    if not config.features.federation or not config.federation.enabled:
        logger.info("Federation is disabled, shutting down sync task")
        return
        
    state = FederationState()
    interval = config.federation.sync_interval
    
    while True:
        try:
            await _execute_synchronization_cycle(config, state)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Federation sync shutting down")
            break
        except Exception as sync_exception:
            logger.error("Federation sync error", error=str(sync_exception))
            await asyncio.sleep(interval)
