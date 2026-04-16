"""
Shared engine resolution and result formatting for MCP tool handlers.

Centralizes database access patterns so individual tool modules stay thin.
Every tool resolves engines through this class instead of touching the pool directly.
"""
from __future__ import annotations

from typing import List, Optional

from mcp.types import TextContent

from config.loader import ConfigManager
from config.schema import DatabaseDefConfig
from db.pool import DatabasePoolManager
from db.engines.base import DatabaseEngine, QueryResult


# ── Safety Caps ──────────────────────────────────────────────────────────
# Prevents large payloads from blowing up the model's context window or RAM.

MAX_RESULT_ROWS = 50
MAX_DIRECTORY_ENTRIES = 100
MAX_FILE_READ_BYTES = 1_048_576  # 1 MB


class EngineResolver:
    """Resolves database engines and configs by alias through the shared pool."""

    @staticmethod
    async def require_engine(alias: str) -> DatabaseEngine:
        """Returns a live engine or raises RuntimeError."""
        engine = await DatabasePoolManager.get_engine(alias)
        if not engine:
            raise RuntimeError(f"Database '{alias}' not found or not connected.")
        return engine

    @staticmethod
    async def require_engine_and_config(alias: str) -> tuple[DatabaseEngine, DatabaseDefConfig]:
        """Returns both engine and its config. Raises on missing alias."""
        config = ConfigManager.get()
        db_config = config.database.get(alias)
        if not db_config:
            raise RuntimeError(f"Database '{alias}' is not configured.")

        engine = await EngineResolver.require_engine(alias)
        return engine, db_config


class ResultFormatter:
    """Converts QueryResult objects into compact TextContent for AI consumption."""

    # ── Mutation Results ─────────────────────────────────────────────────

    @staticmethod
    def format_mutation(affected_rows: int) -> TextContent:
        """Renders a write/delete result as a count message."""
        return TextContent(
            type="text",
            text=f"Query executed successfully. Affected rows: {affected_rows}",
        )

    # ── Select Results ───────────────────────────────────────────────────

    @staticmethod
    def format_select(result: QueryResult) -> TextContent:
        """Renders a read result as a pipe-delimited text table."""
        rows = result.rows or []
        if not rows:
            return TextContent(type="text", text="Query returned 0 rows.")

        total_count = len(rows)
        is_truncated = total_count > MAX_RESULT_ROWS
        visible_rows = rows[:MAX_RESULT_ROWS]
        columns = result.columns or list(visible_rows[0].keys())

        table_body = ResultFormatter._render_table(columns, visible_rows)
        suffix = f"\n({total_count} rows" + (", truncated)" if is_truncated else ")")

        return TextContent(type="text", text=table_body + suffix)

    # ── Column Schema ────────────────────────────────────────────────────

    @staticmethod
    def format_column_line(col) -> str:
        """Renders a single column as a compact descriptor string."""
        pk_tag = " [PK]" if col.primary_key else ""
        null_tag = " [NULL]" if col.nullable else " [NOT NULL]"
        return f"• {col.name}: {col.type}{pk_tag}{null_tag}"

    @staticmethod
    def format_column_inline(col) -> str:
        """Renders a column in compact inline form for table listings."""
        pk_tag = "·PK" if col.primary_key else ""
        return f"{col.name} ({col.type}{pk_tag})"

    # ── Internals ────────────────────────────────────────────────────────

    @staticmethod
    def _render_table(columns: List[str], rows: List[dict]) -> str:
        """Builds a pipe-delimited text table from column names and row dicts."""
        header = " | ".join(columns)
        separator = "-+-".join("-" * len(c) for c in columns)

        lines = []
        for row in rows:
            values = (str(row.get(c, "")) for c in columns)
            lines.append(" | ".join(values))

        return f"{header}\n{separator}\n" + "\n".join(lines)
