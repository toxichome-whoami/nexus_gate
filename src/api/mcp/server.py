"""
MCP Server Factory.

Creates and manages a singleton MCP Server instance that bridges NexusGate's
database and storage infrastructure to AI models via the Model Context Protocol.
Only instantiated when features.mcp is enabled — zero cost otherwise.
"""
import structlog
from mcp.server import Server

from config.loader import ConfigManager

logger = structlog.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# Server Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class MCPServerManager:
    """Manages the singleton MCP Server lifecycle with lazy initialization."""

    _instance: Server | None = None

    @classmethod
    def get(cls) -> Server:
        """Returns the shared MCP server, creating it on first access."""
        if cls._instance is not None:
            return cls._instance

        cls._instance = cls._build_server()
        return cls._instance

    @classmethod
    def _build_server(cls) -> Server:
        """Constructs a fresh MCP server with all tools and resources registered."""
        config = ConfigManager.get()

        server = Server(
            name=config.mcp.server_name,
            version=config.mcp.server_version,
        )

        # Attach handlers from dedicated modules
        cls._attach_tools(server)
        cls._attach_resources(server)

        logger.info(
            "MCP server initialized",
            name=config.mcp.server_name,
            version=config.mcp.server_version,
        )
        return server

    @classmethod
    def _attach_tools(cls, server: Server) -> None:
        """Registers all database and storage tool handlers."""
        from api.mcp.tools import register_all_tools
        register_all_tools(server)

    @classmethod
    def _attach_resources(cls, server: Server) -> None:
        """Registers all resource listing and reading handlers."""
        from api.mcp.resources import register_all_resources
        register_all_resources(server)

    @classmethod
    def shutdown(cls) -> None:
        """Releases the server instance during application teardown."""
        if cls._instance is not None:
            logger.info("MCP server shutting down")
            cls._instance = None
