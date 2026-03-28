from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

from db.engines.base import DatabaseEngine, TableInfo, ColumnInfo, QueryResult
from config.schema import DatabaseDefConfig

class SQLiteEngine(DatabaseEngine):
    def __init__(self, config: DatabaseDefConfig):
        # SQLAlchemy sqlite URI usually looks like sqlite+aiosqlite:///path/to/db.sqlite
        # If url is provided like sqlite:///path to db or just relative path

        uri = config.url
        if not uri.startswith("sqlite+aiosqlite://"):
            if uri.startswith("sqlite://"):
                uri = uri.replace("sqlite://", "sqlite+aiosqlite://")
            else:
                uri = f"sqlite+aiosqlite:///{uri}"

        self.engine: AsyncEngine = create_async_engine(
            uri,
            pool_size=config.pool_max,
            max_overflow=config.pool_max,
            pool_timeout=config.connection_timeout,
            pool_recycle=config.max_lifetime
        )

    async def connect(self) -> None:
        pass # Engine manages its own pool

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
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%';
        """
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            tables = []
            for row in result:
                tables.append(TableInfo(name=row[0], row_count_estimate=0))
            return tables

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        # pragma table_info(table_name)
        sql = f"PRAGMA table_info({table});"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            columns = []
            for row in result:
                # row: cid, name, type, notnull, dflt_value, pk
                columns.append(ColumnInfo(
                    name=row[1],
                    type=row[2],
                    nullable=not row[3],
                    primary_key=bool(row[5])
                ))
            return columns

    async def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> QueryResult:
        async with self.engine.connect() as conn:
            # For writes we need to commit
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
        return "sqlite"
