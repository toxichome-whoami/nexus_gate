# NexusGate — Configuration Reference

Full reference for every key in `config.toml`. See `config.example.toml` for a ready-to-copy template.

---

## `[server]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | string | `"0.0.0.0"` | Bind address |
| `port` | int | `4500` | Listen port |
| `workers` | int | `0` | uvicorn workers (0 = auto) |
| `max_connections` | int | `10000` | Max concurrent connections |
| `request_timeout` | int | `30` | Request timeout in seconds |
| `body_limit` | string | `"10mb"` | Max request body size |
| `tls_cert` | string | `""` | Path to TLS cert (blank = HTTP) |
| `tls_key` | string | `""` | Path to TLS private key |
| `allowed_ips` | list | `[]` | IPs exempt from rate limiting |
| `trusted_proxies` | list | `["127.0.0.1"]` | Trusted reverse proxy IPs |
| `cors_origins` | list | `["*"]` | Allowed CORS origins |
| `shutdown_timeout` | int | `30` | Graceful shutdown timeout |

---

## `[features]`

Feature flags to enable/disable entire subsystems.

| Key | Default | Description |
|-----|---------|-------------|
| `database` | `true` | Enable `/api/db/*` endpoints |
| `storage` | `true` | Enable `/api/fs/*` endpoints |
| `webhook` | `true` | Enable webhook emission |
| `federation` | `false` | Enable `/api/fed/*` and sync |
| `metrics` | `true` | Enable `/metrics` endpoint |
| `playground` | `false` | Enable Swagger UI at `/api/docs` |

---

## `[logging]`

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `"INFO"` | `TRACE \| DEBUG \| INFO \| WARN \| ERROR` |
| `format` | `"json"` | `json \| pretty` |
| `directory` | `"./logs"` | Log file output directory |
| `file_prefix` | `"nexusgate"` | Log filename prefix |
| `max_file_size` | `"50mb"` | Rotate when log exceeds this size |
| `max_files` | `5` | Max rotated log files to keep |
| `stdout` | `true` | Also log to stdout |

---

## `[rate_limit]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable rate limiting |
| `backend` | `"memory"` | `memory \| redis` |
| `redis_url` | `""` | Redis URL (required if backend=redis) |
| `window` | `60` | Window size in seconds |
| `max_requests` | `100` | Max requests per window per key |
| `burst` | `20` | Additional burst allowance |
| `penalty_cooldown` | `300` | IP ban duration after 10 violations |

---

## `[cache]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable caching |
| `backend` | `"memory"` | `memory \| redis` |
| `redis_url` | `""` | Redis URL |
| `max_memory` | `"100mb"` | Memory cache size bound |
| `default_ttl` | `60` | Default TTL in seconds |
| `query_cache` | `true` | Cache DB query results |
| `fs_cache` | `true` | Cache file metadata |

---

## `[webhooks]`

Global webhook delivery settings.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable webhook delivery |
| `timeout` | `5` | HTTP delivery timeout |
| `max_retries` | `3` | Retry attempts on failure |
| `retry_delay` | `2` | Base delay (exponential: delay^attempt) |
| `queue_size` | `10000` | Max pending webhook events |
| `secret_header` | `"X-NexusGate-Signature"` | HMAC header name |

---

## `[webhook.<name>]`

Per-webhook subscription definition.

| Key | Required | Description |
|-----|----------|-------------|
| `url` | yes | Delivery endpoint URL |
| `secret` | yes | HMAC-SHA256 signing secret (≥32 chars) |
| `rule` | yes | Subscription rule (see format below) |
| `headers` | no | Extra headers to send with each delivery |
| `enabled` | `true` | Enable or disable this rule |

**Rule format:** `module.operation@alias:target`

- `module`: `db` or `fs`
- `operation`: `read`, `write`, `delete`, `any`, or `*`
- `alias`: database/storage alias or `*`
- `target`: table name, file path, or `*`

---

## `[database.<alias>]`

| Key | Default | Description |
|-----|---------|-------------|
| `engine` | required | `sqlite \| postgres \| mysql \| mariadb \| mssql` |
| `url` | required | Connection URL |
| `mode` | `"readwrite"` | `readwrite \| readonly \| writeonly` |
| `pool_min` | `2` | Minimum pool connections |
| `pool_max` | `20` | Maximum pool connections |
| `connection_timeout` | `5` | Connect timeout in seconds |
| `idle_timeout` | `300` | Idle connection timeout |
| `max_lifetime` | `1800` | Max connection lifetime |
| `query_whitelist` | `null` | Only allow these SQL operations |
| `query_blacklist` | `["DROP","TRUNCATE","ALTER"]` | Block these SQL operations |
| `dangerous_operations` | `false` | Allow DDL (DROP/ALTER/TRUNCATE) |

---

## `[storage.<alias>]`

| Key | Default | Description |
|-----|---------|-------------|
| `path` | required | Absolute or relative root directory |
| `mode` | `"readwrite"` | `readwrite \| readonly \| writeonly` |
| `limit` | `"5gb"` | Maximum total storage size |
| `chunk_size` | `"10mb"` | Default upload chunk size |
| `max_file_size` | `"500mb"` | Max single file upload size |
| `allowed_extensions` | `[]` | Allowed extensions (empty = all) |
| `blocked_extensions` | `[".exe",".bat",...]` | Blocked extensions |

---

## `[api_key.<name>]`

> [!NOTE]
> This configures **Static API Keys**. You can also generate and manage **Dynamic API Keys** seamlessly via the `/api/admin/keys` endpoint. For security reasons, Dynamic keys cannot be assigned `full_admin` privileges; only static keys managed by developers in this configuration file may act as superadmins.

| Key | Required | Description |
|-----|----------|-------------|
| `secret` | yes | Secret string (≥32 chars) |
| `mode` | `"readwrite"` | `readwrite \| readonly \| writeonly` |
| `db_scope` | `["*"]` | Accessible database aliases |
| `fs_scope` | `["*"]` | Accessible storage aliases |
| `rate_limit_override` | `0` | Per-key rate limit (0 = global) |
| `full_admin` | `false` | Grants access to `/api/admin/*` endpoints |

---

## `[circuit_breaker]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable circuit breaker |
| `failure_threshold` | `5` | Failures before tripping OPEN |
| `success_threshold` | `3` | Successes in HALF_OPEN before CLOSED |
| `timeout` | `30` | Seconds before retry |

---

## `[federation]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable federation |
| `sync_interval` | `30` | Health sync interval in seconds |

## `[federation.incoming.<node_id>]`

Per-node incoming authentication. Each block allows exactly one remote server to connect.

| Key | Required | Description |
|-----|----------|-------------|
| `secret` | yes | Federation secret (≥32 chars, unique per node) |
| `mode` | `"readonly"` | `readwrite \| readonly` |
| `db_scope` | `["*"]` | Accessible database aliases |
| `fs_scope` | `["*"]` | Accessible storage aliases |
| `description` | `""` | Human-readable label for this node |

## `[federation.server.<alias>]`

Outgoing connections to remote NexusGate servers.

| Key | Required | Description |
|-----|----------|-------------|
| `url` | yes | Remote NexusGate base URL |
| `secret` | yes | Federation secret (must match remote's incoming key) |
| `node_id` | yes | Your identity on the remote server |
| `trust_mode` | `"verify"` | `verify` (TLS) or `trust` (skip TLS check) |

