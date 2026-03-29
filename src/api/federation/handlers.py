from fastapi import APIRouter, Depends, Request
import json

from config.loader import ConfigManager
from utils.types import AuthContext
from server.middleware.auth import require_admin
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes
from api.federation.sync import FederationState

from .router import router

@router.get("/servers")
async def list_servers(request: Request, auth: AuthContext = Depends(require_admin)):
    """Show full federation status: outgoing connections + incoming keys."""
    config = ConfigManager.get()
    
    if not config.features.federation:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, "Federation is disabled on this instance.", 501)
        
    state = FederationState()
    
    # ── Outgoing: servers THIS node connects TO ──────────
    outgoing = []
    for alias, srv_config in config.federation.server.items():
        srv_state = state.servers.get(alias, {"status": "unknown"})
        outgoing.append({
            "alias": alias,
            "url": srv_config.url,
            "node_id": srv_config.node_id,
            "status": srv_state.get("status"),
            "latency_ms": srv_state.get("latency_ms"),
            "databases": srv_state.get("databases", {}),
            "storages": srv_state.get("storages", {}),
        })

    # ── Incoming: servers that connect TO this node ──────
    incoming = []
    for node_id, key_config in config.federation.incoming.items():
        incoming.append({
            "node_id": node_id,
            "mode": key_config.mode.value,
            "db_scope": key_config.db_scope,
            "fs_scope": key_config.fs_scope,
            "description": key_config.description,
            # NEVER expose the secret
        })

    return success_response(request, {
        "outgoing": outgoing,
        "outgoing_count": len(outgoing),
        "incoming": incoming,
        "incoming_count": len(incoming),
    })

