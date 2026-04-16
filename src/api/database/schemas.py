from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Request Validations
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Enforces standard raw mappings."""
    sql: str
    params: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None

class InsertRequest(BaseModel):
    """Batches list validations natively."""
    rows: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=1000, description="Max 1000 rows array.")
    row: Optional[Dict[str, Any]] = None

class UpdateRequest(BaseModel):
    """Secures mutation structures."""
    filter: Dict[str, Any]
    update: Dict[str, Any]

class DeleteRequest(BaseModel):
    """Ensures deletions contain constraints."""
    filter: Dict[str, Any]

# ─────────────────────────────────────────────────────────────────────────────
# Parameter Extraction
# ─────────────────────────────────────────────────────────────────────────────

class FetchRowsParams(BaseModel):
    """REST abstraction mapping explicitly bound variables to valid query payloads."""
    page: int = 1
    limit: int = 50
    cursor: Optional[str] = None
    fields: Optional[str] = None
    sort: Optional[str] = None
    order: str = "asc"
    filter: Optional[str] = None
    search: Optional[str] = None
    search_fields: Optional[str] = None
