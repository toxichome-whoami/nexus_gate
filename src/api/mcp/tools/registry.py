"""
Central Tool Registry.

MCP Python SDK requires exactly ONE list_tools() and ONE call_tool() handler.
This registry aggregates tools from multiple domain modules and binds them
to the MCP Server in a single pass.
"""
from typing import Callable, Awaitable
import structlog

from mcp.server import Server
from mcp.types import Tool, TextContent

logger = structlog.get_logger()


class ToolRegistry:
    """Manages dynamic aggregation and registration of MCP tools."""

    def __init__(self):
        self._tools: list[Tool] = []
        self._handlers: dict[str, Callable[..., Awaitable[list[TextContent]]]] = {}

    def clear(self) -> None:
        """Resets all registered tools. Must be called before re-initialization."""
        self._tools.clear()
        self._handlers.clear()

    def register(self, name: str, description: str, input_schema: dict, handler: Callable[..., Awaitable[list[TextContent]]]):
        """Records a tool and its handler callback."""
        self._tools.append(Tool(name=name, description=description, inputSchema=input_schema))
        self._handlers[name] = handler

    def attach_to_server(self, server: Server):
        """Binds the official list/call handlers to the single Server instance."""

        @server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            return self._tools

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
            handler = self._handlers.get(name)
            if not handler:
                raise ValueError(f"Unknown tool: {name}")

            args = arguments or {}
            try:
                return await handler(**args)
            except Exception as e:
                logger.error("Tool execution failed", tool=name, error=str(e))
                # Return a sanitized message without internal implementation details
                return [TextContent(type="text", text=f"Tool '{name}' encountered an error. Please try again or adjust your parameters.")]

# The global registry mapping tools before the server boots.
mcp_tool_registry = ToolRegistry()
