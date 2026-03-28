from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

from db.engines.base import DatabaseEngine, TableInfo, ColumnInfo, QueryResult
from config.schema import DatabaseDefConfig

class MySQLEngine(DatabaseEngine):
    def __init__(self, config: DatabaseDefConfig):
        uri = config.url
        if uri.startswith("mysql://"):
            uri = uri.replace("mysql://", "mysql+aiomysql://")
            
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
        sql = "SHOW TABLES;"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            tables = []
            for row in result:
                tables.append(TableInfo(name=row[0], row_count_estimate=0))
            return tables

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        sql = f"DESCRIBE {table};" # Params can't be used for table names usually in raw text
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            columns = []
            for row in result:
                # row: Field, Type, Null, Key, Default, Extra
                columns.append(ColumnInfo(
                    name=row[0],
                    type=row[1],
                    nullable=(row[2] == 'YES'),
                    primary_key=(row[3] == 'PRI')
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
                rows = [dict(row._mapping) for row in result]
                columns = list(result.keys()) if result.keys() else None
                return QueryResult(columns=columns, rows=rows, affected_rows=result.rowcount)

    @property
    def dialect(self) -> str:
        return "mysql"
