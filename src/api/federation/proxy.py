import httpx
from fastapi import Request
from starlette.responses import StreamingResponse

from config.loader import ConfigManager
from api.errors import NexusGateException, ErrorCodes

# Shared client
_client = None

def get_proxy_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client

async def proxy_request(alias: str, path: str, request: Request, is_database: bool = True):
    """
    Proxy a request to a federated server.
    """
    config = ConfigManager.get()
    
    # 1. Look up server config
    for srv_alias, srv_config in config.federation.server.items():
        if alias.startswith(f"{srv_alias}_"):
            # found the target server
            target_alias = alias[len(srv_alias)+1:] # Strip prefix
            
            # 2. Build remote URL
            base_url = srv_config.url.rstrip("/")
            
            # Identify correct remote path
            # e.g. path input might be empty or specific sub-route
            subpath = path.lstrip("/")
            if is_database:
                remote_url = f"{base_url}/api/db/{target_alias}/{subpath}"
            else:
                remote_url = f"{base_url}/api/fs/{target_alias}/{subpath}"
                
            # append original query string
            query = request.url.query
            if query:
                remote_url += f"?{query}"
                
            # 3. Headers
            headers = dict(request.headers)
            # Remove host header to avoid confusion at remote
            headers.pop("host", None)
            headers.pop("content-length", None) # Let httpx handle it
            
            # Auth with the federation api key, mapping our identity
            headers["Authorization"] = f"Bearer {srv_config.api_key}"
            # Pass original request id
            headers["X-Request-ID"] = getattr(request.state, "request_id", "-")
            
            # 4. Stream response back
            client = get_proxy_client()
            verify = srv_config.trust_mode == "verify"
            
            async def proxy_streamer():
                req = client.build_request(
                    request.method,
                    remote_url,
                    headers=headers,
                    content=request.stream() if request.method in ("POST", "PUT", "PATCH") else None
                )
                try:
                    resp = await client.send(req, stream=True, verify=verify)
                    
                    # Convert response to FastAPI StreamingResponse
                    # Ensure headers are passed through safely
                    pass_headers = {
                        k.lower(): v for k, v in resp.headers.items() 
                        if k.lower() not in ("transfer-encoding", "content-encoding", "connection")
                    }
                    
                    return StreamingResponse(
                        resp.aiter_raw(),
                        status_code=resp.status_code,
                        headers=pass_headers
                    )
                except httpx.RequestError as e:
                    raise NexusGateException(ErrorCodes.FED_SERVER_DOWN, f"Federated server error: {str(e)}", 502)
                    
            return await proxy_streamer()
            
    # If no alias prefix matched
    resource_type = "Database" if is_database else "Storage"
    raise NexusGateException(ErrorCodes.FED_SERVER_DOWN, f"Federated {resource_type} alias '{alias}' not found or unreachable", 404)
