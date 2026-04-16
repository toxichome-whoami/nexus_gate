"""
MCP Tool Definitions.

Each tool wraps an existing NexusGate capability (database query, table listing,
file operations) and exposes it to AI models through the MCP protocol.

Architecture:
  - Tools call the SAME engine/pool/validator stack as REST endpoints.
  - No code is duplicated — query_database uses validate_query + transpile_sql.
  - Results are capped to protect the model's context window.
"""
import os
import structlog
from mcp.server import Server
from mcp.types import Tool, TextContent

from config.loader import ConfigManager
from db.pool import DatabasePoolManager
from api.database.query_parser import validate_query
from db.dialect.transpiler import transpile_sql

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Caps to prevent overwhelming the model's context window or exhausting memory
MAX_RESULT_ROWS = 50
MAX_DIRECTORY_ENTRIES = 100

# MCP tools run with readwrite access; scope is enforced at the API key level
DEFAULT_USER_MODE = "readwrite"


# ─────────────────────────────────────────────────────────────────────────────
# Public Registration
# ─────────────────────────────────────────────────────────────────────────────

def register_all_tools(server: Server) -> None:
    """Attaches all tool handlers to the provided MCP server instance."""

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        """Enumerates all tools available to the AI model."""
        return [
            Tool(
                name="list_databases",
                description="Lists all database aliases configured in this NexusGate instance.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="list_tables",
                description="Lists all tables in a database with their column schemas.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "The database alias to inspect."}
                    },
                    "required": ["database"],
                },
            ),
            Tool(
                name="describe_table",
                description="Returns detailed column metadata for a specific table.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database alias."},
                        "table": {"type": "string", "description": "Table name to describe."}
                    },
                    "required": ["database", "table"],
                },
            ),
            Tool(
                name="query_database",
                description="Executes a SQL query against a NexusGate-managed database. The query is validated and transpiled before execution.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database alias."},
                        "sql": {"type": "string", "description": "SQL query to execute."}
                    },
                    "required": ["database", "sql"],
                },
            ),
            Tool(
                name="list_storages",
                description="Lists all storage aliases configured in this NexusGate instance.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="list_files",
                description="Lists files and directories at the given path in a storage alias.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "storage": {"type": "string", "description": "Storage alias."},
                        "path": {"type": "string", "description": "Directory path to list (default: '/')."}
                    },
                    "required": ["storage"],
                },
            ),
            Tool(
                name="read_file",
                description="Reads and returns the text content of a file in a storage alias. Max 1MB.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "storage": {"type": "string", "description": "Storage alias."},
                        "path": {"type": "string", "description": "Path to the file."}
                    },
                    "required": ["storage", "path"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        """Dispatches an incoming tool call to the correct handler."""
        args = arguments or {}

        try:
            handler = _TOOL_DISPATCH.get(name)
            if not handler:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            return await handler(args)
        except Exception as e:
            logger.error("MCP tool execution failed", tool=name, error=str(e))
            return [TextContent(type="text", text=f"Error: {e}")]

    logger.debug("MCP tools registered")


# ─────────────────────────────────────────────────────────────────────────────
# Database Engine Helpers
# ─────────────────────────────────────────────────────────────────────────────

class DatabaseResolver:
    """Resolves database engines and configs by alias through the shared pool."""

    @staticmethod
    async def get_engine(alias: str):
        """Returns a live database engine. Raises RuntimeError if unavailable."""
        engine = await DatabasePoolManager.get_engine(alias)
        if not engine:
            raise RuntimeError(f"Database '{alias}' not found or not connected.")
        return engine

    @staticmethod
    async def get_engine_and_config(alias: str):
        """Returns both the engine and its DatabaseDefConfig for validation."""
        config = ConfigManager.get()
        db_config = config.database.get(alias)
        if not db_config:
            raise RuntimeError(f"Database '{alias}' is not configured.")

        engine = await DatabaseResolver.get_engine(alias)
        return engine, db_config


# ─────────────────────────────────────────────────────────────────────────────
# Result Formatting
# ─────────────────────────────────────────────────────────────────────────────

class ResultFormatter:
    """Converts QueryResult objects into compact text for AI model consumption."""

    @staticmethod
    def format_mutation(affected_rows: int) -> TextContent:
        """Formats a write/delete result as a simple count message."""
        return TextContent(
            type="text",
            text=f"Query executed successfully. Affected rows: {affected_rows}",
        )

    @staticmethod
    def format_select(result) -> TextContent:
        """Formats a read result as a pipe-delimited text table."""
        rows = result.rows or []
        if not rows:
            return TextContent(type="text", text="Query returned 0 rows.")

        total_count = len(rows)
        is_truncated = total_count > MAX_RESULT_ROWS
        visible_rows = rows[:MAX_RESULT_ROWS]

        columns = result.columns or list(visible_rows[0].keys())
        table_text = ResultFormatter._build_text_table(columns, visible_rows)

        suffix = f"\n({total_count} rows"
        suffix += ", truncated)" if is_truncated else ")"

        return TextContent(type="text", text=table_text + suffix)

    @staticmethod
    def _build_text_table(columns: list, rows: list) -> str:
        """Builds a simple pipe-delimited text table from columns and row dicts."""
        header = " | ".join(columns)
        separator = "-+-".join("-" * len(col) for col in columns)

        data_lines = []
        for row in rows:
            values = [str(row.get(col, "")) for col in columns]
            data_lines.append(" | ".join(values))

        return f"{header}\n{separator}\n" + "\n".join(data_lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tool Handler Implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_list_databases(args: dict) -> list[TextContent]:
    config = ConfigManager.get()
    aliases = list(config.database.keys())
    return [TextContent(
        type="text",
        text=f"Available databases: {', '.join(aliases) or 'None configured'}",
    )]


async def _handle_list_tables(args: dict) -> list[TextContent]:
    database = args["database"]
    engine = await DatabaseResolver.get_engine(database)
    tables = await engine.list_tables()

    if not tables:
        return [TextContent(type="text", text=f"No tables found in '{database}'.")]

    schema_lines = []
    for table in tables:
        columns = await engine.describe_table(table.name)
        column_summary = ", ".join(
            f"{col.name} ({col.type}{'·PK' if col.primary_key else ''})"
            for col in columns
        )
        schema_lines.append(f"• {table.name}: [{column_summary}]")

    return [TextContent(
        type="text",
        text=f"Tables in '{database}':\n" + "\n".join(schema_lines),
    )]


async def _handle_describe_table(args: dict) -> list[TextContent]:
    database, table = args["database"], args["table"]
    engine = await DatabaseResolver.get_engine(database)
    columns = await engine.describe_table(table)

    if not columns:
        return [TextContent(type="text", text=f"Table '{table}' not found.")]

    column_lines = [
        f"• {col.name}: {col.type} "
        f"{'[PK] ' if col.primary_key else ''}"
        f"{'[NULL]' if col.nullable else '[NOT NULL]'}"
        for col in columns
    ]

    return [TextContent(
        type="text",
        text=f"Schema for '{database}.{table}':\n" + "\n".join(column_lines),
    )]


async def _handle_query_database(args: dict) -> list[TextContent]:
    database, sql = args["database"], args["sql"]
    engine, db_config = await DatabaseResolver.get_engine_and_config(database)

    # Validate → transpile → execute (same pipeline as REST)
    safe_sql, operation_type, _target_table = validate_query(
        sql, db_config, DEFAULT_USER_MODE
    )
    transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)
    result = await engine.execute(transpiled_sql)

    # Select-like operations return tabular data; mutations return counts
    is_read_operation = operation_type in ("select", "show", "describe")
    if is_read_operation:
        return [ResultFormatter.format_select(result)]

    return [ResultFormatter.format_mutation(result.affected_rows or 0)]


async def _handle_list_storages(args: dict) -> list[TextContent]:
    config = ConfigManager.get()
    aliases = list(config.storage.keys())
    return [TextContent(
        type="text",
        text=f"Available storages: {', '.join(aliases) or 'None configured'}",
    )]


async def _handle_list_files(args: dict) -> list[TextContent]:
    storage = args["storage"]
    path = args.get("path", "/")
    config = ConfigManager.get()
    storage_config = config.storage.get(storage)

    if not storage_config:
        return [TextContent(type="text", text=f"Storage '{storage}' not found.")]

    target_directory = os.path.join(storage_config.path, path.lstrip("/"))

    if not os.path.isdir(target_directory):
        return [TextContent(type="text", text=f"Path '{path}' is not a directory.")]

    raw_entries = os.listdir(target_directory)
    entry_lines = _format_directory_entries(target_directory, raw_entries)

    return [TextContent(
        type="text",
        text=f"Contents of '{storage}:{path}':\n" + "\n".join(entry_lines),
    )]


async def _handle_read_file(args: dict) -> list[TextContent]:
    storage = args["storage"]
    path = args["path"]
    config = ConfigManager.get()
    storage_config = config.storage.get(storage)

    if not storage_config:
        return [TextContent(type="text", text=f"Storage '{storage}' not found.")]

    file_path = os.path.join(storage_config.path, path.lstrip("/"))

    if not os.path.isfile(file_path):
        return [TextContent(type="text", text=f"File '{path}' does not exist.")]

    file_size = os.path.getsize(file_path)
    if file_size > 1_048_576:  # 1MB cap
        return [TextContent(
            type="text",
            text=f"File '{path}' is too large to read ({file_size:,} bytes). Max: 1MB.",
        )]

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
        return [TextContent(type="text", text=content)]
    except Exception as read_error:
        return [TextContent(type="text", text=f"Error reading file: {read_error}")]


# ─────────────────────────────────────────────────────────────────────────────
# Tool Dispatch Table
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_DISPATCH = {
    "list_databases": _handle_list_databases,
    "list_tables": _handle_list_tables,
    "describe_table": _handle_describe_table,
    "query_database": _handle_query_database,
    "list_storages": _handle_list_storages,
    "list_files": _handle_list_files,
    "read_file": _handle_read_file,
}


# ─────────────────────────────────────────────────────────────────────────────
# Shared Formatting Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _format_directory_entries(base_path: str, entries: list[str]) -> list[str]:
    """Formats directory entries with type icons, capped at MAX_DIRECTORY_ENTRIES."""
    formatted = []
    for entry_name in entries[:MAX_DIRECTORY_ENTRIES]:
        full_path = os.path.join(base_path, entry_name)
        icon = "📁" if os.path.isdir(full_path) else "📄"
        formatted.append(f"  {icon} {entry_name}")

    if len(entries) > MAX_DIRECTORY_ENTRIES:
        formatted.append(f"  ... and {len(entries) - MAX_DIRECTORY_ENTRIES} more")

    return formatted
