"""
Database Resource Provider for MCP.

Exposes database schemas as readable MCP resources so AI models can
load structural context without executing a tool.

Resource URI Format: nexusgate://db/{alias}/schema
"""
from __future__ import annotations

import structlog
from mcp.types import Resource, TextResourceContents

from config.loader import ConfigManager
from db.pool import DatabasePoolManager
from api.mcp.tools.base import ResultFormatter
from api.mcp.resources.registry import mcp_resource_registry, build_error_text_resource

logger = structlog.get_logger()

# Constants
URI_PROTOCOL_PREFIX = "nexusgate://db/"
URI_SCHEMA_SUFFIX = "/schema"


# ── Internal Functions ───────────────────────────────────────────────────

def _extract_database_alias(uri: str) -> str | None:
    """Isolates the database alias from the structured resource URI."""
    if not uri.startswith(URI_PROTOCOL_PREFIX):
        return None
        
    if not uri.endswith(URI_SCHEMA_SUFFIX):
        return None
        
    extracted_alias = uri[len(URI_PROTOCOL_PREFIX):-len(URI_SCHEMA_SUFFIX)]
    return extracted_alias if extracted_alias else None


async def _compile_full_schema(uri: str, alias: str) -> TextResourceContents:
    """Retrieves connections and constructs a complete markdown schema dump."""
    active_engine = await DatabasePoolManager.get_engine(alias)
    
    # Guard against invalid or offline configurations
    if not active_engine:
        return build_error_text_resource(uri, f"Database '{alias}' is offline or disconnected.")

    database_tables = await active_engine.list_tables()
    markdown_lines = [f"# Database Schema: {alias}\n"]

    for table in database_tables:
        table_columns = await active_engine.describe_table(table.name)
        markdown_lines.append(f"## Table: {table.name}")
        markdown_lines.extend(f"  - {ResultFormatter.format_column_line(c)}" for c in table_columns)
        markdown_lines.append("")

    return TextResourceContents(
        uri=uri, 
        mimeType="text/markdown", 
        text="\n".join(markdown_lines)
    )


async def _read_database_schema(uri: str) -> list[TextResourceContents]:
    """Top-level reader hook processing the incoming generic URI query."""
    target_database_alias = _extract_database_alias(uri)
    
    if not target_database_alias:
        return [build_error_text_resource(uri, "Invalid database Resource URI structure.")]

    schema_contents = await _compile_full_schema(uri, target_database_alias)
    return [schema_contents]


# ── Exported Registration ────────────────────────────────────────────────

def register_database_resources() -> None:
    """Configures the unified resource definitions directly into the global map."""
    
    active_configurations = ConfigManager.get().database
    
    for database_alias in active_configurations:
        target_uri = f"{URI_PROTOCOL_PREFIX}{database_alias}{URI_SCHEMA_SUFFIX}"
        
        static_resource = Resource(
            uri=target_uri,
            name=f"Database Context: {database_alias}",
            description=f"Complete table layouts, data types, and primary keys for '{database_alias}'.",
            mimeType="text/markdown",
        )
        mcp_resource_registry.register_listing(static_resource)

    # Attach the generic DB schema processor 
    mcp_resource_registry.register_reader_prefix(URI_PROTOCOL_PREFIX, _read_database_schema)
    logger.debug("Database schematic resources mapped into registry.")
