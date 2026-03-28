from typing import Protocol, List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool

@dataclass
class TableInfo:
    name: str
    row_count_estimate: int
    columns: Optional[List[ColumnInfo]] = None

@dataclass
class QueryResult:
    columns: Optional[List[str]] = None
    rows: Optional[List[Dict[str, Any]]] = None
    affected_rows: Optional[int] = None

class DatabaseEngine(Protocol):
    
    async def connect(self) -> None:
        """Initialize connection pool."""
        ...
        
    async def disconnect(self) -> None:
        """Close connection pool."""
        ...
        
    async def health_check(self) -> bool:
        """Check if database is reachable."""
        ...
        
    async def list_tables(self) -> List[TableInfo]:
        """List all tables in the database."""
        ...
        
    async def describe_table(self, table: str) -> List[ColumnInfo]:
        """Get column information for a specific table."""
        ...
        
    async def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> QueryResult:
        """Execute parameterized SQL query."""
        ...
        
    @property
    def dialect(self) -> str:
        """Return sqlglot dialect name (e.g., 'postgres', 'mysql', 'sqlite')."""
        ...
