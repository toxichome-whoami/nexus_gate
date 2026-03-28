from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Union

class QueryRequest(BaseModel):
    sql: str
    params: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None

class InsertRequest(BaseModel):
    rows: Optional[List[Dict[str, Any]]] = None
    row: Optional[Dict[str, Any]] = None

class UpdateRequest(BaseModel):
    filter: Dict[str, Any]
    update: Dict[str, Any]

class DeleteRequest(BaseModel):
    filter: Dict[str, Any]

class FetchRowsParams(BaseModel):
    page: int = 1
    limit: int = 50
    cursor: Optional[str] = None
    fields: Optional[str] = None
    sort: Optional[str] = None
    order: str = "asc"
    filter: Optional[str] = None
    search: Optional[str] = None
    search_fields: Optional[str] = None
