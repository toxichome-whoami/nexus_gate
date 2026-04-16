"""
MCP Server Lifecycle Manager.

Owns the singleton MCP Server instance. Lazy-initialized on first access
so that zero memory is consumed when features.mcp is disabled.
All tool and resource registrations are delegated to dedicated modules.
"""
import structlog
from typing import Optional

from mcp.server import Server
from config.loader import ConfigManager

logger = structlog.get_logger()


class MCPServerManager:
    """Thread-safe singleton controlling the MCP Server lifecycle."""

    _server: Optional[Server] = None

    @classmethod
    def get(cls) -> Server:
        """Returns the shared server instance, building it on first call."""
        if cls._server is not None:
            return cls._server

        cls._server = cls._build()
        return cls._server

    @classmethod
    def shutdown(cls) -> None:
        """Releases the server instance during application teardown."""
        if cls._server is None:
            return

        logger.info("MCP server released")
        cls._server = None

    @classmethod
    def _build(cls) -> Server:
        """Constructs a fresh Server and attaches all handlers."""
        config = ConfigManager.get()

        server = Server(
            name=config.mcp.server_name,
            version=config.mcp.server_version,
        )

        cls._attach_handlers(server)

        logger.info(
            "MCP server initialized",
            name=config.mcp.server_name,
            version=config.mcp.server_version,
        )
        return server

    @classmethod
    def _attach_handlers(cls, server: Server) -> None:
        """Registers tools and resources via their dedicated modules."""
        
        # Pull Registries
        from api.mcp.tools.registry import mcp_tool_registry
        from api.mcp.resources.registry import mcp_resource_registry
        
        # Pull Implementations
        from api.mcp.tools.database_tools import register_database_tools
        from api.mcp.tools.storage_tools import register_storage_tools
        from api.mcp.resources.database_resources import register_database_resources
        from api.mcp.resources.storage_resources import register_storage_resources

        # Prevent duplicate entries on re-initialization
        mcp_tool_registry.clear()
        mcp_resource_registry.clear()

        # Load tools logic natively
        register_database_tools()
        register_storage_tools()
        
        # Load resources logic natively
        register_database_resources()
        register_storage_resources()
        
        # Bind everything directly up to the single active router instance
        mcp_tool_registry.attach_to_server(server)
        mcp_resource_registry.attach_to_server(server)

