import base64
import httpx
from fastapi import Request
from starlette.responses import StreamingResponse
from starlette.background import BackgroundTask

from config.loader import ConfigManager
from api.errors import NexusGateException, ErrorCodes

# Shared clients (one per trust mode)
_clients: dict = {}

def get_proxy_client(verify_ssl: bool = True) -> httpx.AsyncClient:
    """Manages globally persistent proxy connections mapping trust states."""
    global _clients
    if verify_ssl not in _clients:
        _clients[verify_ssl] = httpx.AsyncClient(timeout=30.0, verify=verify_ssl)
    return _clients[verify_ssl]

# ─────────────────────────────────────────────────────────────────────────────
# Proxy Request Builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_remote_url(srv_config, target_alias: str, path: str, query: str, is_database: bool) -> str:
    """Generates the absolute upstream endpoint bypassing string concats."""
    base_url = srv_config.url.rstrip("/")
    subpath = path.lstrip("/")
    
    remote_url = f"{base_url}/api/db/{target_alias}/{subpath}" if is_database else f"{base_url}/api/fs/{target_alias}/{subpath}"
    return f"{remote_url}?{query}" if query else remote_url

def _build_proxy_headers(request: Request, srv_config) -> dict:
    """Constructs transmission limits erasing native Host overlays."""
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    encoded_secret = base64.b64encode(srv_config.secret.encode("utf-8")).decode("utf-8")
    headers["X-Federation-Secret"] = encoded_secret
    headers["X-Federation-Node"] = srv_config.node_id
    headers["X-Request-ID"] = getattr(request.state, "request_id", "-")
    
    return headers

async def _stream_proxy_execution(client: httpx.AsyncClient, request: Request, remote_url: str, headers: dict) -> StreamingResponse:
    """Dispatches background streaming sockets returning bound payloads."""
    req = client.build_request(
        request.method,
        remote_url,
        headers=headers,
        content=request.stream() if request.method in ("POST", "PUT", "PATCH") else None
    )
    
    try:
        resp = await client.send(req, stream=True)
        pass_headers = {
            k.lower(): v for k, v in resp.headers.items() 
            if k.lower() not in ("transfer-encoding", "content-encoding", "connection", "content-length")
        }

        return StreamingResponse(
            resp.aiter_bytes(),
            status_code=resp.status_code,
            headers=pass_headers,
            background=BackgroundTask(resp.aclose)
        )
    except httpx.RequestError as req_error:
        raise NexusGateException(ErrorCodes.FED_SERVER_DOWN, f"Federated server error: {str(req_error)}", 502)

# ─────────────────────────────────────────────────────────────────────────────
# Primary Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

async def proxy_request(alias: str, path: str, request: Request, is_database: bool = True) -> StreamingResponse:
    """Entrypoint binding exact aliases targeting mapped proxies natively."""
    config = ConfigManager.get()

    for srv_alias, srv_config in config.federation.server.items():
        if alias.startswith(f"{srv_alias}_"):
            target_alias = alias[len(srv_alias)+1:] 
            remote_url = _build_remote_url(srv_config, target_alias, path, request.url.query, is_database)
            headers = _build_proxy_headers(request, srv_config)
            
            client = get_proxy_client(srv_config.trust_mode == "verify")
            return await _stream_proxy_execution(client, request, remote_url, headers)

    resource_type = "Database" if is_database else "Storage"
    raise NexusGateException(ErrorCodes.FED_SERVER_DOWN, f"Federated {resource_type} alias '{alias}' not found or unreachable", 404)
