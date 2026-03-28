from enum import Enum
from typing import TypedDict, Any, Dict, List, Optional
from pydantic import BaseModel

class HealthStatusEnum(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UP = "up"
    DOWN = "down"

class StatusEnum(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"

class ServerMode(str, Enum):
    READWRITE = "readwrite"
    READONLY = "readonly"
    WRITEONLY = "writeonly"

class EventModule(str, Enum):
    DB = "db"
    FS = "fs"

class EventOperation(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ANY = "any"

class DbEngineType(str, Enum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    SQLITE = "sqlite"
    MSSQL = "mssql"
    ORACLE = "oracle"
    MARIADB = "mariadb"
    DB2 = "db2"
    COCKROACHDB = "cockroachdb"
    
class FileType(str, Enum):
    FILE = "file"
    DIR = "directory"
    ALL = "all"

class AuthContext(BaseModel):
    api_key_name: str
    mode: ServerMode
    db_scope: List[str]
    fs_scope: List[str]
    rate_limit_override: int
    full_admin: bool = False

class RequestMeta(BaseModel):
    request_id: str
    timestamp: str
    duration_ms: float
    server: str
    version: str
    federated: Optional[bool] = None
    proxy_latency_ms: Optional[float] = None

class ErrorDetails(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None

class ResponseEnvelope(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[ErrorDetails] = None
    meta: RequestMeta
    links: Optional[Dict[str, str]] = None
