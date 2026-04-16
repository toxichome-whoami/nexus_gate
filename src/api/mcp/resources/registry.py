"""
Central Resource Registry.

MCP Python SDK requires exactly ONE list_resources() and ONE read_resource() handler.
This registry aggregates context resources from multiple domain modules
(database schemas, storage settings) into a single hook.
"""
from typing import Callable, Awaitable
import structlog

from mcp.server import Server
from mcp.types import Resource, TextResourceContents

logger = structlog.get_logger()

# Type map for our dynamic resource reading
ResourceReader = Callable[[str], Awaitable[list[TextResourceContents]]]


class ResourceRegistry:
    """Manages dynamic aggregation of MCP Resources avoiding decorator overrides."""

    def __init__(self):
        self._resources: list[Resource] = []
        # Maps a URI prefix (e.g. "nexusgate://db/") to its dedicated reading function
        self._readers: dict[str, ResourceReader] = {}

    def clear(self) -> None:
        """Resets all registered resources. Must be called before re-initialization."""
        self._resources.clear()
        self._readers.clear()

    def register_listing(self, resource: Resource) -> None:
        """Adds a static resource listing to the global context manifest."""
        self._resources.append(resource)

    def register_reader_prefix(self, prefix: str, reader_func: ResourceReader) -> None:
        """Assigns a callback to handle any URI strings matching the target prefix."""
        self._readers[prefix] = reader_func

    def attach_to_server(self, server: Server) -> None:
        """Binds the aggregated list/read handlers to the physical Server instance."""

        @server.list_resources()
        async def handle_list_resources() -> list[Resource]:
            return self._resources

        @server.read_resource()
        async def handle_read_resource(uri: str) -> list[TextResourceContents]:
            for prefix, reader_func in self._readers.items():
                if uri.startswith(prefix):
                    try:
                        return await reader_func(uri)
                    except Exception as execution_error:
                        logger.error("Failed to read resource", uri=uri, error=str(execution_error))
                        # Sanitized error — no internal details exposed to the client
                        return [TextResourceContents(uri=uri, mimeType="text/plain", text="Resource read failed due to an internal error.")]

            return [TextResourceContents(uri=uri, mimeType="text/plain", text="Unknown resource.")]

# Global registry mapped out before the server boots.
mcp_resource_registry = ResourceRegistry()

def build_error_text_resource(target_uri: str, error_message: str) -> TextResourceContents:
    """Returns a standardized plain-text error resource envelope."""
    return TextResourceContents(
        uri=target_uri,
        mimeType="text/plain",
        text=error_message
    )
