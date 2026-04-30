from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from config.schema import DatabaseDefConfig
from db.engines.base import ColumnInfo, DatabaseEngine, QueryResult, TableInfo

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_uri(raw_uri: str) -> str:
    """Pre-configures URIs to inject exactly the correct asynchronous wrapper driver."""
    if raw_uri.startswith("mysql://"):
        return raw_uri.replace("mysql://", "mysql+aiomysql://")
    return raw_uri


def _is_mutation_query(sql: str) -> bool:
    """Identifies explicitly state-altering queries cleanly."""
    return (
        sql.strip()
        .upper()
        .startswith(
            ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "CREATE", "ALTER")
        )
    )


async def _execute_mutation(conn, statement, params: dict) -> QueryResult:
    """Executes destructive changes mapped with explicitly awaited limits."""
    result = await conn.execute(statement, params)
    await conn.commit()
    return QueryResult(affected_rows=result.rowcount)


async def _execute_read(conn, statement, params: dict) -> QueryResult:
    """Pulls complex read layouts gracefully bypassing empty mappings safely."""
    result = await conn.execute(statement, params)
    rows = [dict(row._mapping) for row in result] if result.returns_rows else []
    columns = list(result.keys()) if result.keys() else []

    if not result.returns_rows:
        await conn.commit()

    return QueryResult(columns=columns, rows=rows, affected_rows=result.rowcount)


# ─────────────────────────────────────────────────────────────────────────────
# Core Driver Protocol Implementation
# ─────────────────────────────────────────────────────────────────────────────


class MySQLEngine(DatabaseEngine):
    """Provides ultra-fast async-first hooks traversing MySQL distributions."""

    def __init__(self, config: DatabaseDefConfig):
        standardized_uri = _normalize_uri(config.url)
        overflow_buffer = max(0, config.pool_max - config.pool_min)

        self.engine: AsyncEngine = create_async_engine(
            standardized_uri,
            pool_size=config.pool_min,
            max_overflow=overflow_buffer,
            pool_timeout=config.connection_timeout,
            pool_recycle=config.max_lifetime,
            pool_pre_ping=True,
        )

    async def connect(self) -> None:
        """Handled entirely natively by declarative connection strategies."""
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
        sql = "SHOW TABLES;"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            return [TableInfo(name=row[0], row_count_estimate=0) for row in result]

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        """Maps declarative descriptions targeting explicit table structural limits."""
        sql = f"DESCRIBE {table};"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            return [
                ColumnInfo(
                    name=row[0],
                    type=row[1],
                    nullable=(row[2] == "YES"),
                    primary_key=(row[3] == "PRI"),
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
        return "mysql"
