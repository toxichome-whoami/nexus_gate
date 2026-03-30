"""MSSQL (SQL Server) engine implementation using aioodbc."""
from typing import List, Dict, Any, Optional

try:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
    from sqlalchemy import text
    HAS_AIOODBC = True
except ImportError:
    HAS_AIOODBC = False

from db.engines.base import DatabaseEngine, TableInfo, ColumnInfo, QueryResult
from config.schema import DatabaseDefConfig


class MSSQLEngine(DatabaseEngine):
    """Microsoft SQL Server async engine via aioodbc + SQLAlchemy."""

    def __init__(self, config: DatabaseDefConfig):
        if not HAS_AIOODBC:
            raise RuntimeError(
                "MSSQL support requires: pip install aioodbc pyodbc. "
                "Install via: pip install nexusgate[mssql]"
            )

        uri = config.url
        # Normalize URL format
        if uri.startswith("mssql://"):
            uri = uri.replace("mssql://", "mssql+aioodbc://")
        elif uri.startswith("sqlserver://"):
            uri = uri.replace("sqlserver://", "mssql+aioodbc://")

        self.engine: AsyncEngine = create_async_engine(
            uri,
            pool_size=config.pool_min,
            max_overflow=config.pool_max - config.pool_min if config.pool_max > config.pool_min else 0,
            pool_timeout=config.connection_timeout,
            pool_recycle=config.max_lifetime,
            pool_pre_ping=True,
        )

    async def connect(self) -> None:
        pass  # SQLAlchemy manages pool lazily

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
        sql = """
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
        """
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            return [TableInfo(name=row[0], row_count_estimate=0) for row in result]

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        sql = """
        SELECT
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.IS_NULLABLE,
            CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS IS_PK
        FROM INFORMATION_SCHEMA.COLUMNS c
        LEFT JOIN (
            SELECT ku.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            AND ku.TABLE_NAME = :table
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

    async def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> QueryResult:
        if params is None:
            params = {}
        async with self.engine.connect() as conn:
            if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                result = await conn.execute(text(sql), params)
                await conn.commit()
                return QueryResult(affected_rows=result.rowcount)
            else:
                result = await conn.execute(text(sql), params)
                rows = [dict(row._mapping) for row in result]
                columns = list(result.keys()) if result.keys() else None
                return QueryResult(columns=columns, rows=rows, affected_rows=result.rowcount)

    @property
    def dialect(self) -> str:
        return "tsql"
