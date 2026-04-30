"""
Database Tools for MCP.

Exposes NexusGate's database capabilities as MCP tools that AI models
can invoke. Every tool reuses the existing validation/transpilation
pipeline — no query bypasses the AST parser.

Permission mode and database scope are derived from the authenticated
session context, not hardcoded.
"""

from __future__ import annotations

import structlog

from api.database.query_parser import validate_query
from api.mcp.session_auth import get_mcp_auth
from api.mcp.tools.base import EngineResolver, ResultFormatter
from api.mcp.tools.registry import mcp_tool_registry
from config.loader import ConfigManager
from db.dialect.transpiler import transpile_sql
from mcp.types import TextContent

logger = structlog.get_logger()

_READ_OPERATIONS = frozenset({"select", "show", "describe"})


# -- Scope Enforcement -----------------------------------------------------


def _check_database_scope(alias: str) -> bool:
    """Verifies the authenticated session has access to the requested database alias."""
    auth = get_mcp_auth()
    # Empty db_scope or "*" means unrestricted access to all databases
    if not auth.db_scope or "*" in auth.db_scope:
        return True
    return alias in auth.db_scope


# -- Handlers --------------------------------------------------------------


async def _list_databases() -> list[TextContent]:
    """Lists database aliases visible to the authenticated session."""
    auth = get_mcp_auth()
    all_aliases = list(ConfigManager.get().database.keys())

    # Filter by scope if restrictions exist and it's not a global wildcard
    if auth.db_scope and "*" not in auth.db_scope:
        visible = [a for a in all_aliases if a in auth.db_scope]
    else:
        visible = all_aliases

    label = ", ".join(visible) if visible else "None available"
    return [TextContent(type="text", text=f"Available databases: {label}")]


async def _list_tables(database: str) -> list[TextContent]:
    if not _check_database_scope(database):
        return [
            TextContent(
                type="text",
                text=f"Access denied: database '{database}' is outside your scope.",
            )
        ]

    engine = await EngineResolver.require_engine(database)
    tables = await engine.list_tables()

    if not tables:
        return [TextContent(type="text", text=f"No tables in '{database}'.")]

    lines = []
    for table in tables:
        columns = await engine.describe_table(table.name)
        col_summary = ", ".join(
            ResultFormatter.format_column_inline(c) for c in columns
        )
        lines.append(f"- {table.name}: [{col_summary}]")

    return [
        TextContent(type="text", text=f"Tables in '{database}':\n" + "\n".join(lines))
    ]


async def _describe_table(database: str, table: str) -> list[TextContent]:
    if not _check_database_scope(database):
        return [
            TextContent(
                type="text",
                text=f"Access denied: database '{database}' is outside your scope.",
            )
        ]

    engine = await EngineResolver.require_engine(database)
    columns = await engine.describe_table(table)

    if not columns:
        return [TextContent(type="text", text=f"Table '{table}' not found.")]

    lines = [ResultFormatter.format_column_line(c) for c in columns]
    return [
        TextContent(
            type="text", text=f"Schema for '{database}.{table}':\n" + "\n".join(lines)
        )
    ]


async def _query_database(database: str, sql: str) -> list[TextContent]:
    if not _check_database_scope(database):
        return [
            TextContent(
                type="text",
                text=f"Access denied: database '{database}' is outside your scope.",
            )
        ]

    # Derive the permission mode from the authenticated session, not a hardcoded default
    auth = get_mcp_auth()
    user_mode = auth.mode.value if hasattr(auth.mode, "value") else str(auth.mode)

    engine, db_config = await EngineResolver.require_engine_and_config(database)

    safe_sql, operation_type, _ = validate_query(sql, db_config, user_mode)
    transpiled = transpile_sql(safe_sql, to_dialect=engine.dialect)
    result = await engine.execute(transpiled)

    if operation_type in _READ_OPERATIONS:
        return [ResultFormatter.format_select(result)]

    return [ResultFormatter.format_mutation(result.affected_rows or 0)]


# -- Registration ----------------------------------------------------------


def register_database_tools() -> None:
    """Registers DB capabilities into the global tool registry."""

    mcp_tool_registry.register(
        name="list_databases",
        description="Lists all database aliases configured and available natively in NexusGate.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_databases,
    )

    mcp_tool_registry.register(
        name="list_tables",
        description="Lists all tables inside a specific database along with inline column previews.",
        input_schema={
            "type": "object",
            "properties": {"database": {"type": "string"}},
            "required": ["database"],
        },
        handler=_list_tables,
    )

    mcp_tool_registry.register(
        name="describe_table",
        description="Extracts detailed column definitions (types, PKs, Nullability) for a specific table.",
        input_schema={
            "type": "object",
            "properties": {"database": {"type": "string"}, "table": {"type": "string"}},
            "required": ["database", "table"],
        },
        handler=_describe_table,
    )

    mcp_tool_registry.register(
        name="query_database",
        description="Safely executes AST-validated SQL strings against the target database connection.",
        input_schema={
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "sql": {"type": "string", "description": "Raw SQL query string"},
            },
            "required": ["database", "sql"],
        },
        handler=_query_database,
    )
