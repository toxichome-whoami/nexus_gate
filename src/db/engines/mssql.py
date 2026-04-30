"""MSSQL (SQL Server) engine implementation using aioodbc."""

from typing import Any, Dict, List, Optional

try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

    HAS_AIOODBC = True
except ImportError:
    HAS_AIOODBC = False
    create_async_engine: Any = None
    AsyncEngine: Any = None
    text: Any = None

from config.schema import DatabaseDefConfig
from db.engines.base import ColumnInfo, DatabaseEngine, QueryResult, TableInfo

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_uri(raw_uri: str) -> str:
    """Safely transposes human-readable schemes to driver-specific representations."""
    if raw_uri.startswith("mssql://"):
        return raw_uri.replace("mssql://", "mssql+aioodbc://")
    if raw_uri.startswith("sqlserver://"):
        return raw_uri.replace("sqlserver://", "mssql+aioodbc://")
    return raw_uri


def _is_mutation_query(sql: str) -> bool:
    """Determines if the raw payload enforces write locks or mutates schema/data."""
    return (
        sql.strip()
        .upper()
        .startswith(
            ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "CREATE", "ALTER")
        )
    )


async def _execute_mutation(conn, statement, params: dict) -> QueryResult:
    """Dispatches a mutation and forces an explicit commit."""
    result = await conn.execute(statement, params)
    await conn.commit()
    return QueryResult(affected_rows=result.rowcount)


async def _execute_read(conn, statement, params: dict) -> QueryResult:
    """Resolves standard non-mutating statements into explicit mapping lists."""
    result = await conn.execute(statement, params)
    rows = [dict(row._mapping) for row in result] if result.returns_rows else []
    columns = list(result.keys()) if result.keys() else []

    if not result.returns_rows:
        # Commit dynamically to free isolation level row locks gracefully
        await conn.commit()

    return QueryResult(columns=columns, rows=rows, affected_rows=result.rowcount)


# ─────────────────────────────────────────────────────────────────────────────
# MSSQL Driver
# ─────────────────────────────────────────────────────────────────────────────


class MSSQLEngine(DatabaseEngine):
    """Microsoft SQL Server async engine via aioodbc + SQLAlchemy."""

    def __init__(self, config: DatabaseDefConfig):
        if not HAS_AIOODBC:
            raise RuntimeError(
                "MSSQL support requires: pip install aioodbc pyodbc. "
                "Install via: pip install nexusgate[mssql]"
            )

        standardized_uri = _normalize_uri(config.url)
        overflow_buffer = max(0, config.pool_max - config.pool_min)

        self.engine: Any = create_async_engine(
            standardized_uri,
            pool_size=config.pool_min,
            max_overflow=overflow_buffer,
            pool_timeout=config.connection_timeout,
            pool_recycle=config.max_lifetime,
            pool_pre_ping=True,
        )

    async def connect(self) -> None:
        """Driver pools are natively managed by the lazy-loading SQLAlchemy core."""
        pass

    async def disconnect(self) -> None:
        await self.engine.dispose()

    async def health_check(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def list_tables(self) -> List[TableInfo]:
        sql = "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            return [TableInfo(name=row[0], row_count_estimate=0) for row in result]

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        sql = """
        SELECT
            c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE,
            CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS IS_PK
        FROM INFORMATION_SCHEMA.COLUMNS c
        LEFT JOIN (
            SELECT ku.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' AND ku.TABLE_NAME = :table
        ) pk ON pk.COLUMN_NAME = c.COLUMN_NAME
        WHERE c.TABLE_NAME = :table
        ORDER BY c.ORDINAL_POSITION
        """
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), {"table": table})
            return [
                ColumnInfo(
                    name=row[0],
                    type=row[1],
                    nullable=(row[2] == "YES"),
                    primary_key=bool(row[3]),
                )
                for row in result
            ]

    async def execute(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> QueryResult:
        query_params = params or {}
        statement = text(sql)

        async with self.engine.connect() as conn:
            if _is_mutation_query(sql):
                return await _execute_mutation(conn, statement, query_params)
            return await _execute_read(conn, statement, query_params)

    @property
    def dialect(self) -> str:
        return "tsql"
