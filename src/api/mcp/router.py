"""
MCP SSE Transport Router.

Exposes two endpoints that together form the MCP transport layer:
  - GET  /sse      → Establishes a persistent Server-Sent Events stream
  - POST /messages → Receives JSON-RPC messages from the connected client

Auth is enforced by NexusGate's existing middleware stack (rate limit, WAF, bearer).
"""
import structlog
from fastapi import APIRouter, Request
from mcp.server.sse import SseServerTransport
from starlette.responses import Response

from api.mcp.server import MCPServerManager

logger = structlog.get_logger()

router = APIRouter(tags=["MCP"])

# ─────────────────────────────────────────────────────────────────────────────
# Transport Layer
# ─────────────────────────────────────────────────────────────────────────────

# Shared transport instance — maps the message endpoint path for SSE clients
_sse_transport = SseServerTransport("/api/v1/mcp/messages")


@router.get("/sse")
async def handle_sse_connection(request: Request) -> Response:
    """
    Opens a persistent SSE stream for an MCP client session.
    The client connects here first, then sends tool calls via /messages.
    Connection ends when the client disconnects.
    """
    server = MCPServerManager.get()

    async with _sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        read_stream, write_stream = streams
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )

    return Response(status_code=200)


@router.post("/messages")
async def handle_mcp_message(request: Request) -> Response:
    """
    Receives a JSON-RPC message from the MCP client and routes it
    to the matching tool or resource handler registered on the server.
    """
    return await _sse_transport.handle_post_message(
        request.scope, request.receive, request._send
    )
