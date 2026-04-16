"""
MCP Resource Definitions.

Exposes database schemas and storage metadata as readable MCP resources.
Resources let AI models "read" structured data without executing a tool —
useful for context-loading before the model starts reasoning.

Resource URI format:
  nexusgate://db/{alias}/schema   → Full table + column schema dump
  nexusgate://fs/{alias}/info     → Storage configuration summary
"""
import structlog
from mcp.server import Server
from mcp.types import Resource, TextResourceContents

from config.loader import ConfigManager
from db.pool import DatabasePoolManager

logger = structlog.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# Public Registration
# ─────────────────────────────────────────────────────────────────────────────

def register_all_resources(server: Server) -> None:
    """Attaches resource listing and reading handlers to the MCP server."""

    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        """Enumerates all available resources — one per database and storage alias."""
        config = ConfigManager.get()
        resources = []

        # Database schema resources
        for alias in config.database:
            resources.append(Resource(
                uri=f"nexusgate://db/{alias}/schema",
                name=f"Database Schema: {alias}",
                description=f"Full table and column schema for the '{alias}' database.",
                mimeType="text/plain",
            ))

        # Storage info resources
        for alias in config.storage:
            resources.append(Resource(
                uri=f"nexusgate://fs/{alias}/info",
                name=f"Storage Info: {alias}",
                description=f"Configuration and limits for the '{alias}' storage.",
                mimeType="text/plain",
            ))

        return resources

    @server.read_resource()
    async def handle_read_resource(uri: str) -> list[TextResourceContents]:
        """Routes a resource read request to the appropriate handler by URI."""
        parsed = _parse_resource_uri(uri)
        if not parsed:
            return [_error_content(uri, "Invalid resource URI format.")]

        module, alias, action = parsed
        handler = _RESOURCE_HANDLERS.get((module, action))

        if not handler:
            return [_error_content(uri, f"Unknown resource type: {module}/{action}")]

        return [await handler(uri, alias)]

    logger.debug("MCP resources registered")


# ─────────────────────────────────────────────────────────────────────────────
# URI Parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_resource_uri(uri: str) -> tuple[str, str, str] | None:
    """
    Parses a nexusgate:// URI into (module, alias, action).
    Returns None if the URI is malformed.
    """
    stripped = uri.replace("nexusgate://", "")
    parts = stripped.split("/")

    if len(parts) < 3:
        return None

    return parts[0], parts[1], parts[2]


# ─────────────────────────────────────────────────────────────────────────────
# Database Schema Reader
# ─────────────────────────────────────────────────────────────────────────────

class SchemaRenderer:
    """Renders database table schemas into human-readable text."""

    @staticmethod
    async def render(uri: str, alias: str) -> TextResourceContents:
        """Compiles a full schema dump for a single database alias."""
        engine = await DatabasePoolManager.get_engine(alias)

        if not engine:
            return _error_content(uri, f"Database '{alias}' is not connected.")

        tables = await engine.list_tables()
        output_lines = [f"# Database: {alias}\n"]

        for table in tables:
            columns = await engine.describe_table(table.name)
            output_lines.append(f"## {table.name}")

            for col in columns:
                pk_tag = " [PRIMARY KEY]" if col.primary_key else ""
                null_tag = " NULL" if col.nullable else " NOT NULL"
                output_lines.append(f"  - {col.name}: {col.type}{pk_tag}{null_tag}")

            output_lines.append("")  # Blank line between tables

        return TextResourceContents(
            uri=uri,
            mimeType="text/plain",
            text="\n".join(output_lines),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Storage Info Reader
# ─────────────────────────────────────────────────────────────────────────────

class StorageInfoRenderer:
    """Renders storage alias configuration into human-readable text."""

    @staticmethod
    async def render(uri: str, alias: str) -> TextResourceContents:
        """Returns configuration summary for a storage alias."""
        config = ConfigManager.get()
        storage_config = config.storage.get(alias)

        if not storage_config:
            return _error_content(uri, f"Storage '{alias}' is not configured.")

        info_lines = [
            f"# Storage: {alias}",
            f"Path: {storage_config.path}",
            f"Mode: {storage_config.mode.value}",
            f"Limit: {storage_config.limit}",
            f"Max File Size: {storage_config.max_file_size}",
            f"Blocked Extensions: {', '.join(storage_config.blocked_extensions)}",
        ]

        return TextResourceContents(
            uri=uri,
            mimeType="text/plain",
            text="\n".join(info_lines),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Handler Dispatch Table
# ─────────────────────────────────────────────────────────────────────────────

# Lookup table eliminates nested if/else chains for routing resource reads
_RESOURCE_HANDLERS = {
    ("db", "schema"): SchemaRenderer.render,
    ("fs", "info"): StorageInfoRenderer.render,
}


# ─────────────────────────────────────────────────────────────────────────────
# Shared Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _error_content(uri: str, message: str) -> TextResourceContents:
    """Creates a standardized error response for resource read failures."""
    return TextResourceContents(uri=uri, mimeType="text/plain", text=message)
