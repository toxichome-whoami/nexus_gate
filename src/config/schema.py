from typing import List, Dict, Optional, Literal, Any, Union
from pydantic import BaseModel, Field, field_validator
import re

from utils.types import ServerMode, DbEngineType

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 4500
    workers: int = 0
    max_connections: int = 10000
    request_timeout: int = 30
    body_limit: str = "10mb"
    tls_cert: str = ""
    tls_key: str = ""
    allowed_ips: List[str] = Field(default_factory=list)
    trusted_proxies: List[str] = Field(default_factory=lambda: ["127.0.0.1"])
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    shutdown_timeout: int = 30

class FeaturesConfig(BaseModel):
    database: bool = True
    storage: bool = True
    webhook: bool = True
    federation: bool = False
    metrics: bool = True
    playground: bool = False

class LoggingConfig(BaseModel):
    level: Literal["TRACE", "DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    format: Literal["json", "pretty"] = "json"
    directory: str = "./logs"
    file_prefix: str = "nexusgate"
    max_file_size: str = "50mb"
    max_files: int = 5
    stdout: bool = True

class RateLimitConfig(BaseModel):
    enabled: bool = True
    backend: Literal["memory", "redis"] = "memory"
    redis_url: str = ""
    window: int = 60
    max_requests: int = 100
    burst: int = 20
    penalty_cooldown: int = 300

class CacheConfig(BaseModel):
    enabled: bool = True
    backend: Literal["memory", "redis"] = "memory"
    redis_url: str = ""
    max_memory: str = "100mb"
    default_ttl: int = 60
    query_cache: bool = True
    fs_cache: bool = True

class WebhookGlobalConfig(BaseModel):
    enabled: bool = True
    timeout: int = 5
    max_retries: int = 3
    retry_delay: int = 2
    queue_size: int = 10000
    secret_header: str = "X-NexusGate-Signature"

class WebhookDefConfig(BaseModel):
    url: str
    secret: str
    rule: str
    headers: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator('rule')
    def validate_rule(cls, v):
        if not re.match(r'^(db|fs)\.(read|write|delete|any)@[^:]+:[^:]+$', v):
            raise ValueError("Rule must match format: module.operation@alias:target")
        return v

class DatabaseDefConfig(BaseModel):
    engine: DbEngineType
    url: str
    mode: ServerMode = ServerMode.READWRITE
    pool_min: int = 2
    pool_max: int = 20
    connection_timeout: int = 5
    idle_timeout: int = 300
    max_lifetime: int = 1800
    query_whitelist: Optional[List[str]] = None
    query_blacklist: Optional[List[str]] = Field(default_factory=lambda: ["DROP", "TRUNCATE", "ALTER"])
    dangerous_operations: bool = False

class StorageDefConfig(BaseModel):
    path: str
    mode: ServerMode = ServerMode.READWRITE
    limit: str = "5gb"
    chunk_size: str = "10mb"
    allowed_extensions: List[str] = Field(default_factory=list)
    blocked_extensions: List[str] = Field(default_factory=lambda: [".exe", ".bat", ".sh", ".cmd", ".ps1"])
    max_file_size: str = "500mb"
    access: List[str] = Field(default_factory=lambda: ["*"])

class ApiKeyDefConfig(BaseModel):
    mode: ServerMode = ServerMode.READWRITE
    secret: str
    db_scope: List[str] = Field(default_factory=lambda: ["*"])
    fs_scope: List[str] = Field(default_factory=lambda: ["*"])
    rate_limit_override: int = 0

    @field_validator('secret')
    def validate_secret_length(cls, v):
        if len(v) < 32:
            raise ValueError("API key secret must be at least 32 characters long")
        return v

class FedServerConfig(BaseModel):
    url: str
    api_key: str
    alias: str
    trust_mode: Literal["verify", "trust"] = "verify"

class FederationConfig(BaseModel):
    enabled: bool = False
    sync_interval: int = 30
    server: Dict[str, FedServerConfig] = Field(default_factory=dict)

class CircuitBreakerConfig(BaseModel):
    enabled: bool = True
    failure_threshold: int = 5
    success_threshold: int = 3
    timeout: int = 30

class NexusGateConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    webhooks: WebhookGlobalConfig = Field(default_factory=WebhookGlobalConfig)
    webhook: Dict[str, WebhookDefConfig] = Field(default_factory=dict)
    database: Dict[str, DatabaseDefConfig] = Field(default_factory=dict)
    storage: Dict[str, StorageDefConfig] = Field(default_factory=dict)
    api_key: Dict[str, ApiKeyDefConfig] = Field(default_factory=dict)
    federation: FederationConfig = Field(default_factory=FederationConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
