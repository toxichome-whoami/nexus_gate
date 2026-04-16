# NexusGate API Reference

## Authentication

All endpoints (except `/health` and `/ready`) require Bearer token authentication.

**Format:**
```
Authorization: Bearer base64(<key_name>:<secret>)
```

**cURL Example:**
```bash
# To generate the token in bash:
# TOKEN=$(echo -n "admin:your_secret_here" | base64)

curl -X GET "http://localhost:4500/api/v1/db/databases" \
     -H "Authorization: Bearer $TOKEN"
```

---

## Core Endpoints

### 1. Server Info & Feature Flags
```bash
curl -X GET "http://localhost:4500/" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. Kubernetes Readiness Probe
Does not require authentication.
```bash
curl -X GET "http://localhost:4500/ready"
```

### 3. Deep Health Check
```bash
curl -X GET "http://localhost:4500/health" \
     -H "Authorization: Bearer <TOKEN>"
```

### 4. Metrics (Prometheus)
```bash
curl -X GET "http://localhost:4500/metrics" \
     -H "Authorization: Bearer <TOKEN>"
```

### 5. OpenAPI JSON Spec
```bash
curl -X GET "http://localhost:4500/api/v1/api/spec" \
     -H "Authorization: Bearer <TOKEN>"
```

---

## Database API `/api/v1/db`

### 1. List Databases
```bash
curl -X GET "http://localhost:4500/api/v1/db/databases" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. List Tables
```bash
curl -X GET "http://localhost:4500/api/v1/db/main_db/tables" \
     -H "Authorization: Bearer <TOKEN>"
```

### 3. Execute Raw SQL
> [!CAUTION]
> Raw SQL is validated by AST parser. Dangerous operations blocked per config.

```bash
curl -X POST "http://localhost:4500/api/v1/db/main_db/query" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "sql": "SELECT * FROM users WHERE id = :id",
           "params": {"id": 42}
         }'
```

### 4. Fetch Rows (with filtering)
```bash
curl -G "http://localhost:4500/api/v1/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     --data-urlencode "page=1" \
     --data-urlencode "limit=50" \
     --data-urlencode "sort=created_at" \
     --data-urlencode "order=desc" \
     --data-urlencode 'filter={"active":true,"age":{"$gte":18}}' \
     --data-urlencode "fields=id,name,email"
```

### 5. Insert Rows
```bash
curl -X POST "http://localhost:4500/api/v1/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "rows": [{"name": "Alice", "active": true}]
         }'
```

### 6. Update Rows
```bash
curl -X PUT "http://localhost:4500/api/v1/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "filter": {"id": 42},
           "update": {"active": false}
         }'
```

### 7. Delete Rows
```bash
curl -X DELETE "http://localhost:4500/api/v1/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "filter": {"id": 42}
         }'
```

---

## Storage API `/api/v1/fs`

### 1. List Storages
```bash
curl -X GET "http://localhost:4500/api/v1/fs/storages" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. List Folder
```bash
curl -X GET "http://localhost:4500/api/v1/fs/local_fs/list?path=/subfolder" \
     -H "Authorization: Bearer <TOKEN>"
```

### 3. Download File or Folder
```bash
# Inline view of a file
curl -X GET "http://localhost:4500/api/v1/fs/local_fs/download?path=/image.png&inline=true" \
     -H "Authorization: Bearer <TOKEN>" -O

# Download with image resizing
curl -X GET "http://localhost:4500/api/v1/fs/local_fs/download?path=/image.png&width=300&height=200" \
     -H "Authorization: Bearer <TOKEN>" -o thumb.png

# Download folder as ZIP archive automatically
curl -X GET "http://localhost:4500/api/v1/fs/local_fs/download?path=/reports_folder" \
     -H "Authorization: Bearer <TOKEN>" -o reports.zip
```

### 4. Direct Upload (Small Files)
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -F "action=direct" \
     -F "path=/uploads/file.txt" \
     -F "file=@/path/to/local/file.txt"
```

### 5. Chunked Upload (Large Files)
```bash
# Step 1: Initiate
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action":"initiate", "filename":"video.mp4", "path":"/uploads/video.mp4", "total_size":104857600, "checksum_sha256":"abc123..."}'
# Note the `upload_id` returned

# Step 2: Upload Chunks
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -F "action=chunk" \
     -F "upload_id=upl_xxx" \
     -F "chunk_index=0" \
     -F "chunk_hash=sha256_of_chunk" \
     -F "file=@chunk0.bin"

# Step 3: Finalize
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action":"finalize", "upload_id":"upl_xxx"}'
```

### 6. File Actions

All file actions are sent as `POST` requests to `/{alias}/action` with a JSON body containing the `action` field.

> [!NOTE]
> The `info` and `exists` actions are available to **read-only** API keys. All other actions require `readwrite` or `writeonly` mode.

#### Rename / Move / Copy
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "action": "rename",
           "source": "/old.txt",
           "target": "/new.txt"
         }'
```

| Action | Description |
|--------|-------------|
| `rename` | Rename a file or directory |
| `move` | Move a file or directory (alias for rename) |
| `copy` | Copy a file or directory to a new location |

#### Delete
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action": "delete", "source": "/unwanted.txt"}'
```

#### Create Directory
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action": "mkdir", "source": "/new_folder"}'
```

#### File Info
Returns detailed metadata: name, type, size, human-readable size, MIME type, timestamps, and item count for directories.
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action": "info", "source": "/reports/Q1.pdf"}'
```
**Response:**
```json
{
  "success": true,
  "data": {
    "action": "info",
    "source": "/reports/Q1.pdf",
    "info": {
      "name": "Q1.pdf",
      "type": "file",
      "size": 2457600,
      "size_human": "2.34 MB",
      "mime_type": "application/pdf",
      "modified": "2026-03-15T10:30:00",
      "created": "2026-03-01T08:00:00"
    }
  }
}
```

#### Check Existence
Lightweight boolean check — does not transfer file data.
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action": "exists", "source": "/config/app.yml"}'
```
**Response:**
```json
{
  "success": true,
  "data": {
    "action": "exists",
    "source": "/config/app.yml",
    "exists": true
  }
}
```

#### Bulk Delete
Delete multiple files/directories in a single request. Each item reports its own success/failure status.
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "action": "bulk_delete",
           "sources": ["/tmp/old1.log", "/tmp/old2.log", "/tmp/cache"]
         }'
```
**Response:**
```json
{
  "success": true,
  "data": {
    "action": "bulk_delete",
    "results": [
      {"source": "/tmp/old1.log", "status": "success"},
      {"source": "/tmp/old2.log", "status": "success"},
      {"source": "/tmp/cache", "status": "success"}
    ]
  }
}
```

#### Bulk Move
Move multiple files/directories in a single request. Provide an `operations` array of `{source, target}` pairs.
```bash
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "action": "bulk_move",
           "operations": [
             {"source": "/inbox/file1.txt", "target": "/archive/file1.txt"},
             {"source": "/inbox/file2.txt", "target": "/archive/file2.txt"}
           ]
         }'
```
**Response:**
```json
{
  "success": true,
  "data": {
    "action": "bulk_move",
    "results": [
      {"source": "/inbox/file1.txt", "target": "/archive/file1.txt", "status": "success"},
      {"source": "/inbox/file2.txt", "target": "/archive/file2.txt", "status": "success"}
    ]
  }
}
```

---

## Admin API `/api/v1/admin`

All admin endpoints require an API key with `full_admin` set to `true` in `config.toml`.

> [!IMPORTANT]
> Dynamic keys created via the API **cannot** have `full_admin` privileges. Only static keys defined in `config.toml` can be superadmins. You also cannot ban or revoke the key you are currently using (self-lockout protection).

---

### API Key Management

#### List All API Keys
Shows both static (`config.toml`) and dynamic (`SQLite`) keys. Secrets are never exposed.
```bash
curl -X GET "http://localhost:4500/api/v1/admin/keys" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Create Dynamic API Key
Generates a new key with a cryptographically secure secret (32-64 chars). The raw secret and a ready-to-use Bearer token are returned **once** and cannot be retrieved again.
```bash
curl -X POST "http://localhost:4500/api/v1/admin/keys" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "service_bot_1",
           "mode": "readwrite",
           "db_scope": ["*"],
           "fs_scope": ["public_assets"],
           "rate_limit_override": 1000
         }'
```

#### Revoke API Key
Dynamic keys are deleted from SQLite. Static keys (from `config.toml`) are permanently banned instead.
```bash
curl -X DELETE "http://localhost:4500/api/v1/admin/keys/service_bot_1" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Update Dynamic API Key
Partially update an existing dynamic key's properties. The API key secret **cannot** be changed. Static keys from `config.toml` cannot be modified.
```bash
curl -X PATCH "http://localhost:4500/api/v1/admin/keys/actions" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "service_bot_1",
           "mode": "readonly",
           "db_scope": ["analytics_db"],
           "rate_limit_override": 500
         }'
```

---

### Ban Management

#### List Active Bans
```bash
curl -X GET "http://localhost:4500/api/v1/admin/bans" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Ban an IP Address
Set `duration_seconds` to `null` for a permanent ban.
```bash
curl -X POST "http://localhost:4500/api/v1/admin/bans/ip" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "ip": "192.168.1.100",
           "reason": "Abuse of login endpoints",
           "duration_seconds": 3600
         }'
```

#### Unban an IP Address
```bash
curl -X DELETE "http://localhost:4500/api/v1/admin/bans/ip/192.168.1.100" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Ban an API Key
```bash
curl -X POST "http://localhost:4500/api/v1/admin/bans/key" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "key_name": "legacy_key_2",
           "reason": "Compromised credential",
           "duration_seconds": null
         }'
```

#### Unban an API Key
```bash
curl -X DELETE "http://localhost:4500/api/v1/admin/bans/key/legacy_key_2" \
     -H "Authorization: Bearer <TOKEN>"
```

---

### Circuit Breaker Management

#### View Circuit Breaker States
```bash
curl -X GET "http://localhost:4500/api/v1/admin/circuit-breakers" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Reset a Circuit Breaker
```bash
curl -X POST "http://localhost:4500/api/v1/admin/circuit-breakers/main_db/reset" \
     -H "Authorization: Bearer <TOKEN>"
```

---

### Database Management

#### List Databases
Shows all connected databases (both static and dynamic). Connection URLs are always redacted.
```bash
curl -X GET "http://localhost:4500/api/v1/admin/databases" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Create Dynamic Database
Adds a new database connection at runtime. Persisted in SQLite and survives restarts.
```bash
curl -X POST "http://localhost:4500/api/v1/admin/databases" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "analytics_db",
           "engine": "postgres",
           "url": "postgresql://user:pass@localhost:5432/analytics",
           "mode": "readonly",
           "pool_min": 2,
           "pool_max": 10,
           "dangerous_operations": false
         }'
```

#### Delete Dynamic Database
Only removes databases that were added via the API. Static databases from `config.toml` cannot be deleted.
```bash
curl -X DELETE "http://localhost:4500/api/v1/admin/databases/analytics_db" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Update Dynamic Database
Partially update an existing dynamic database's connection settings. `query_whitelist` and `query_blacklist` can only be set in `config.toml`.
```bash
curl -X PATCH "http://localhost:4500/api/v1/admin/databases/actions" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "analytics_db",
           "mode": "readwrite",
           "pool_max": 20
         }'
```

---

### Webhook Management

#### List Webhooks
Shows all registered webhooks. HMAC secrets are always redacted.
```bash
curl -X GET "http://localhost:4500/api/v1/admin/webhooks" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Create Dynamic Webhook
Adds a new webhook listener at runtime. The HMAC signing secret is **auto-generated** (32–64 chars) and returned **once** — store it securely. Persisted in SQLite.
```bash
curl -X POST "http://localhost:4500/api/v1/admin/webhooks" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "audit_hook",
           "url": "https://example.com/api/intercept",
           "rule": "db.delete@main_db:*",
           "enabled": true
         }'
```

#### Delete Dynamic Webhook
Only removes webhooks that were added via the API. Static webhooks from `config.toml` cannot be deleted.
```bash
curl -X DELETE "http://localhost:4500/api/v1/admin/webhooks/audit_hook" \
     -H "Authorization: Bearer <TOKEN>"
```

#### Update Dynamic Webhook
Partially update an existing dynamic webhook's properties. The HMAC secret **cannot** be changed.
```bash
curl -X PATCH "http://localhost:4500/api/v1/admin/webhooks/actions" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "audit_hook",
           "url": "https://new-server.com/api/intercept",
           "enabled": false
         }'
```

---

### System Introspection

#### View Live Config
Returns the full running configuration with all secrets, URLs, and API keys redacted.
```bash
curl -X GET "http://localhost:4500/api/v1/admin/config" \
     -H "Authorization: Bearer <TOKEN>"
```

#### View Rate Limit Overrides
Shows global rate limit settings and any per-key overrides (from both static and dynamic keys).
```bash
curl -X GET "http://localhost:4500/api/v1/admin/rate-limits" \
     -H "Authorization: Bearer <TOKEN>"
```

---

## Federation API `/api/v1/fed`

### 1. List Federated Servers
```bash
curl -X GET "http://localhost:4500/api/v1/fed/servers" \
     -H "Authorization: Bearer <TOKEN>"
```

---

## MCP API `/api/v1/mcp`

> [!NOTE]
> MCP (Model Context Protocol) must be enabled via `features.mcp = true` in `config.toml`. When disabled, these endpoints do not exist and consume zero resources.

The MCP API exposes NexusGate's database and storage capabilities to AI models (Claude, Gemini, custom agents) through the standardized [Model Context Protocol](https://modelcontextprotocol.io/).

### 1. Connect via SSE
Establishes a persistent Server-Sent Events stream. The MCP client connects here first.
```bash
curl -N "http://localhost:4500/api/v1/mcp/sse" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. Send JSON-RPC Message
Used by the MCP client to send tool calls and resource reads after connecting via SSE.
```bash
curl -X POST "http://localhost:4500/api/v1/mcp/messages" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Available Tools

| Tool | Parameters | Description |
|---|---|---|
| `list_databases` | — | Lists all configured database aliases |
| `list_tables` | `database: str` | Lists tables with column schemas |
| `describe_table` | `database: str`, `table: str` | Detailed column metadata |
| `query_database` | `database: str`, `sql: str` | Executes validated SQL (AST-parsed) |
| `list_storages` | — | Lists all configured storage aliases |
| `list_files` | `storage: str`, `path: str` | Lists files/dirs at a path |
| `read_file` | `storage: str`, `path: str` | Reads text file content (max 1MB) |

### Available Resources

| Resource URI | Description |
|---|---|
| `nexusgate://db/{alias}/schema` | Full table + column schema dump |
| `nexusgate://fs/{alias}/info` | Storage configuration summary |

### Client Configuration

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "nexusgate": {
      "url": "http://localhost:4500/api/v1/mcp/sse",
      "headers": {
        "Authorization": "Bearer <your_base64_token>"
      }
    }
  }
}
```

**MCP Inspector** (for testing):
```bash
npx @modelcontextprotocol/inspector
# URL: http://localhost:4500/api/v1/mcp/sse
# Add Authorization header with your Bearer token
```

---

## Filter Syntax


Filters accept a JSON object of field-to-operator mappings:

| Operator | Description | Example |
|----------|-------------|---------|
| `$eq` | Equal | `{"age": {"$eq": 25}}` or `{"age": 25}` |
| `$ne` | Not equal | `{"status": {"$ne": "banned"}}` |
| `$gt` | Greater than | `{"score": {"$gt": 50}}` |
| `$gte` | Greater or equal | `{"age": {"$gte": 18}}` |
| `$lt` | Less than | `{"price": {"$lt": 100}}` |
| `$lte` | Less or equal | `{"rank": {"$lte": 10}}` |
| `$in` | In list | `{"role": {"$in": ["admin","mod"]}}` |
| `$nin` | Not in list | `{"role": {"$nin": ["banned"]}}` |
| `$like` | SQL LIKE | `{"email": {"$like": "%@gmail.com"}}` |
| `$null` | IS NULL / NOT NULL | `{"deleted_at": {"$null": true}}` |
| `$between` | BETWEEN | `{"age": {"$between": [18, 65]}}` |
