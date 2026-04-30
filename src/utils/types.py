from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class HealthStatusEnum(str, Enum):
    """Reflects the operational state of a node or subsystem."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UP = "up"
    DOWN = "down"


class StatusEnum(str, Enum):
    """Denotes structural network availability."""

    ONLINE = "online"
    OFFLINE = "offline"


class ServerMode(str, Enum):
    """Access control permission boundary sets."""

    READWRITE = "readwrite"
    READONLY = "readonly"
    WRITEONLY = "writeonly"


class EventModule(str, Enum):
    """Categorizes webhook propagation boundaries."""

    DB = "db"
    FS = "fs"


class EventOperation(str, Enum):
    """Categorizes structural dispatch actions over webhook pipelines."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ANY = "any"


class DbEngineType(str, Enum):
    """SQL Dialect drivers actively supported by the API translation routing."""

    POSTGRES = "postgres"
    MYSQL = "mysql"
    SQLITE = "sqlite"
    MSSQL = "mssql"
    ORACLE = "oracle"
    MARIADB = "mariadb"
    DB2 = "db2"
    COCKROACHDB = "cockroachdb"


class FileType(str, Enum):
    """File storage target discriminators."""

    FILE = "file"
    DIR = "directory"
    ALL = "all"


# ─────────────────────────────────────────────────────────────────────────────
# Data Transfer Objects
# ─────────────────────────────────────────────────────────────────────────────


class AuthContext(BaseModel):
    """Aggregated security claims verified at the middleware entry point."""

    model_config = ConfigDict(frozen=True)

    api_key_name: str
    mode: ServerMode
    db_scope: List[str]
    fs_scope: List[str]
    rate_limit_override: int
    full_admin: bool = False


class RequestMeta(BaseModel):
    """Tracing telemetry attached to all outbound payload envelopes."""

    model_config = ConfigDict()

    request_id: str
    timestamp: str
    duration_ms: float
    server: str
    version: str
    federated: Optional[bool] = None
    proxy_latency_ms: Optional[float] = None


class ErrorDetails(BaseModel):
    """Standardized failure representation adhering to RFC specifications."""

    model_config = ConfigDict()

    code: str
    message: str
    details: Optional[Any] = None


class ResponseEnvelope(BaseModel):
    """The master root wrapper wrapping ALL REST payloads dispatched by NexusGate."""

    model_config = ConfigDict()

    success: bool
    data: Optional[Any] = None
    error: Optional[ErrorDetails] = None
    meta: RequestMeta
    links: Optional[Dict[str, str]] = None
