from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

from db.engines.base import DatabaseEngine, TableInfo, ColumnInfo, QueryResult
from config.schema import DatabaseDefConfig

class PostgresEngine(DatabaseEngine):
    def __init__(self, config: DatabaseDefConfig):
        uri = config.url
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql+asyncpg://")
        elif uri.startswith("postgresql://"):
            uri = uri.replace("postgresql://", "postgresql+asyncpg://")

        self.engine: AsyncEngine = create_async_engine(
            uri,
            pool_size=config.pool_min,
            max_overflow=config.pool_max - config.pool_min if config.pool_max > config.pool_min else 0,
            pool_timeout=config.connection_timeout,
            pool_recycle=config.max_lifetime
        )

    async def connect(self) -> None:
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
        sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            tables = []
            for row in result:
                tables.append(TableInfo(name=row[0], row_count_estimate=0))
            return tables

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        sql = """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = :table;
        """
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), {"table": table})
            columns = []
            for row in result:
                columns.append(ColumnInfo(
                    name=row[0],
                    type=row[1],
                    nullable=(row[2] == 'YES'),
                    primary_key=False # Simplified, requires querying pg_index for PK info
                ))
            return columns

    async def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> QueryResult:
        async with self.engine.connect() as conn:
            if params is None:
                params = {}
            if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                result = await conn.execute(text(sql), params)
                await conn.commit()
                return QueryResult(affected_rows=result.rowcount)
            else:
                result = await conn.execute(text(sql), params)
                rows = []
                columns = []
                if result.returns_rows:
                    rows = [dict(row._mapping) for row in result]
                    columns = list(result.keys()) if result.keys() else []
                if not result.returns_rows:
                    await conn.commit()
                return QueryResult(columns=columns, rows=rows, affected_rows=result.rowcount)

    @property
    def dialect(self) -> str:
        return "postgres"
