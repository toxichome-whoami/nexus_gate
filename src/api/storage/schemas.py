from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

class StorageItem(BaseModel):
    name: str
    type: Literal["file", "directory"]
    size: Optional[int] = None
    size_human: Optional[str] = None
    mime_type: Optional[str] = None
    modified: Optional[str] = None
    created: Optional[str] = None
    checksum_sha256: Optional[str] = None
    download_url: Optional[str] = None
    items_count: Optional[int] = None

class UploadInitRequest(BaseModel):
    action: Literal["initiate"]
    filename: str
    path: str
    total_size: int
    mime_type: Optional[str] = None
    checksum_sha256: str
    chunk_size: Optional[int] = None

class ActionRequest(BaseModel):
    action: Literal["rename", "move", "copy", "delete", "mkdir", "info", "exists", "bulk_delete", "bulk_move", "archive", "extract"]
    source: Optional[str] = None
    target: Optional[str] = None
    sources: Optional[List[str]] = None
    operations: Optional[List[Dict[str, str]]] = None
