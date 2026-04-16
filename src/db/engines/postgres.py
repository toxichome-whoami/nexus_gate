from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

from db.engines.base import DatabaseEngine, TableInfo, ColumnInfo, QueryResult
from config.schema import DatabaseDefConfig

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_uri(raw_uri: str) -> str:
    """Pre-configures URI strings to inject the rapid `asyncpg` bindings natively."""
    base = raw_uri.replace("postgres://", "postgresql+asyncpg://")
    return base.replace("postgresql://", "postgresql+asyncpg://")

def _extract_ssl_kwargs(uri: str) -> dict:
    """Infers mandatory encryption protocols straight from URI assignments."""
    if "ssl=true" in uri or "sslmode=require" in uri:
        return {"ssl": True}
    return {}

def _is_mutation_query(sql: str) -> bool:
    """Identifies explicitly state-altering queries clearly."""
    return sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "CREATE", "ALTER"))

async def _execute_mutation(conn, statement, params: dict) -> QueryResult:
    """Executes destructive changes mapping with explicitly triggered pool commits."""
    result = await conn.execute(statement, params)
    await conn.commit()
    return QueryResult(affected_rows=result.rowcount)

async def _execute_read(conn, statement, params: dict) -> QueryResult:
    """Executes standard transactional bounds fetching explicitly nested layouts."""
    result = await conn.execute(statement, params)
    rows = [dict(row._mapping) for row in result] if result.returns_rows else []
    columns = list(result.keys()) if result.keys() else []
    
    if not result.returns_rows:
        await conn.commit()
        
    return QueryResult(columns=columns, rows=rows, affected_rows=result.rowcount)

# ─────────────────────────────────────────────────────────────────────────────
# Core Driver
# ─────────────────────────────────────────────────────────────────────────────

class PostgresEngine(DatabaseEngine):
    """Provides high-performance hooks interacting directly over pg_hba compliant links."""
    
    def __init__(self, config: DatabaseDefConfig):
        standardized_uri = _normalize_uri(config.url)
        ssl_params = _extract_ssl_kwargs(config.url)
        overflow_buffer = max(0, config.pool_max - config.pool_min)

        self.engine: AsyncEngine = create_async_engine(
            standardized_uri,
            connect_args=ssl_params,
            pool_size=config.pool_min,
            max_overflow=overflow_buffer,
            pool_timeout=config.connection_timeout,
            pool_recycle=config.max_lifetime,
            pool_pre_ping=True
        )

    async def connect(self) -> None:
        """Handled intrinsically by declarative connections."""
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
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql))
            return [TableInfo(name=row[0], row_count_estimate=0) for row in result]

    async def describe_table(self, table: str) -> List[ColumnInfo]:
        sql = "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = :table;"
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), {"table": table})
            return [
                ColumnInfo(
                    name=row[0],
                    type=row[1],
                    nullable=(row[2] == 'YES'),
                    primary_key=False
                ) for row in result
            ]

    async def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> QueryResult:
        query_params = params or {}
        statement = text(sql)
        
        async with self.engine.connect() as conn:
            if _is_mutation_query(sql):
                return await _execute_mutation(conn, statement, query_params)
            return await _execute_read(conn, statement, query_params)

    @property
    def dialect(self) -> str:
        return "postgres"
