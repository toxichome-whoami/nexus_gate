"""
MCP SSE Transport Router.

Mounts two FastAPI endpoints forming the MCP transport layer:
  GET  /sse      - Persistent Server-Sent Events stream (client connects here)
  POST /messages - JSON-RPC message receiver (tool calls, resource reads)

Authentication is enforced manually via header extraction since the MCP SDK
bypasses FastAPI's Depends() injection by using raw ASGI send/receive.
"""
import base64
import structlog
from fastapi import APIRouter, Request
from mcp.server.sse import SseServerTransport
from starlette.responses import Response, JSONResponse

from api.mcp.server import MCPServerManager
from api.mcp.session_auth import set_mcp_auth, clear_mcp_auth
from server.middleware.auth import (
    _parse_bearer_token,
    _evaluate_network_bans,
    _get_dynamic_key_context,
    _get_static_key_context,
)
from config.loader import ConfigManager
from fastapi.security import HTTPAuthorizationCredentials

logger = structlog.get_logger()

router = APIRouter(tags=["MCP"])

# Shared transport — maps the JSON-RPC endpoint path for SSE clients
_transport = SseServerTransport("/api/v1/mcp/messages")


class ASGIPassThroughResponse(Response):
    """
    A null response that prevents FastAPI from sending a duplicate
    http.response.start after the MCP SDK has already written its own
    response directly through the raw ASGI send callable.
    """
    async def __call__(self, scope, receive, send) -> None:
        pass


# -- Authentication --------------------------------------------------------

def _authenticate_from_request(request: Request) -> None:
    """
    Extracts the token from either the 'Authorization' header or the 'token'
    query parameter, then validates it against the credential stores.
    """
    auth_header = request.headers.get("authorization", "")
    token = ""

    if auth_header:
        # Extract from Header: "Bearer <token>"
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
             raise _auth_error("Invalid Authorization scheme. Expected 'Bearer <token>'.", 401)
    else:
        # Fallback to Query Param: "?token=..."
        token = request.query_params.get("token", "")
        if not token:
            raise _auth_error("Missing Authorization header or token query parameter.", 401)

    credentials = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)

    try:
        key_name, secret = _parse_bearer_token(credentials)
    except Exception:
        raise _auth_error("Malformed token. Expected Base64(key_name:secret).", 401)

    config = ConfigManager.get()

    # Check network-level bans (IP + key)
    try:
        _evaluate_network_bans(request, key_name)
    except Exception as ban_error:
        raise _auth_error(str(ban_error), 403)

    # Resolve credentials: dynamic keys first, then static config keys
    try:
        auth_ctx = _get_dynamic_key_context(key_name, secret)
        if not auth_ctx:
            auth_ctx = _get_static_key_context(key_name, secret, config)
    except Exception as auth_error:
        raise _auth_error(str(auth_error), 401)

    set_mcp_auth(auth_ctx)


class _AuthenticationError(Exception):
    """Internal signal carrying a pre-built JSON error response."""
    def __init__(self, response: JSONResponse):
        self.response = response


def _auth_error(message: str, status_code: int) -> _AuthenticationError:
    """Constructs a standardized auth rejection."""
    return _AuthenticationError(JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": "MCP_AUTH_FAILED",
                "message": message,
            }
        }
    ))


# -- Endpoints -------------------------------------------------------------

@router.get("/sse")
async def handle_sse_connection(request: Request) -> Response:
    """Opens a persistent SSE stream for an MCP client session."""
    try:
        _authenticate_from_request(request)
    except _AuthenticationError as auth_err:
        return auth_err.response

    server = MCPServerManager.get()

    try:
        async with _transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        clear_mcp_auth()

    return ASGIPassThroughResponse()


@router.post("/messages")
async def handle_mcp_message(request: Request) -> Response:
    """Routes a JSON-RPC message to the matching tool or resource handler."""
    try:
        _authenticate_from_request(request)
    except _AuthenticationError as auth_err:
        return auth_err.response

    try:
        await _transport.handle_post_message(
            request.scope, request.receive, request._send
        )
    finally:
        clear_mcp_auth()

    return ASGIPassThroughResponse()
