from fastapi import APIRouter, Depends, Request
import json

from config.loader import ConfigManager
from utils.types import AuthContext
from server.middleware.auth import get_auth_context
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes
from api.federation.sync import FederationState

from .router import router

@router.get("/servers")
async def list_servers(request: Request, auth: AuthContext = Depends(get_auth_context)):
    config = ConfigManager.get()
    
    if not config.features.federation:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, "Federation is disabled on this instance.", 501)
        
    state = FederationState()
    
    servers = []
    for alias, srv_config in config.federation.server.items():
        srv_state = state.servers.get(alias, {"status": "unknown"})
        servers.append({
            "alias": alias,
            "url": srv_config.url, # Only metadata returned to admin typically, could mask
            "status": srv_state.get("status"),
            "latency_ms": srv_state.get("latency_ms"),
            "databases": srv_state.get("databases", {}),
            "storages": srv_state.get("storages", {})
        })
        
    return success_response(request, {"servers": servers})
