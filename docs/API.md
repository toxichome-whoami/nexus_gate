# NexusGate API Reference

## Authentication

All endpoints (except `/health` and `/ready`) require Bearer token authentication.

**Format:**
```
Authorization: Bearer base64(<key_name>:<secret>)
```

**Python example:**
```python
import base64, requests

token = base64.b64encode(b"admin:your_secret_here").decode()
headers = {"Authorization": f"Bearer {token}"}
```

---

## Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Server info & feature flags |
| GET | `/ready` | Kubernetes readiness probe |
| GET | `/health` | Deep health of all subsystems |
| GET | `/metrics` | OpenMetrics/Prometheus metrics |
| GET | `/api/spec` | OpenAPI JSON spec |

---

## Database API `/api/db`

### List Databases
```
GET /api/db/databases
```

### List Tables
```
GET /api/db/{db_name}/tables
```

### Execute Raw SQL
```
POST /api/db/{db_name}/query
Content-Type: application/json

{
  "sql": "SELECT * FROM users WHERE id = :id",
  "params": {"id": 42}
}
```

> [!CAUTION]
> Raw SQL is validated by AST parser. Dangerous operations blocked per config.

### Fetch Rows (with filtering)
```
GET /api/db/{db_name}/{table}/rows
  ?page=1
  &limit=50
  &sort=created_at&order=desc
  &filter={"active":true,"age":{"$gte":18}}
  &fields=id,name,email
```

### Insert Rows
```
POST /api/db/{db_name}/{table}/rows
{
  "rows": [{"name": "Alice", "active": true}]
}
```

### Update Rows
```
PUT /api/db/{db_name}/{table}/rows
{
  "filter": {"id": 42},
  "update": {"active": false}
}
```

### Delete Rows
```
DELETE /api/db/{db_name}/{table}/rows
{
  "filter": {"id": 42}
}
```

---

## Storage API `/api/fs`

### List Storages
```
GET /api/fs/storages
```

### List Folder
```
GET /api/fs/{alias}/list?path=/subfolder
```

### Download File
```
GET /api/fs/{alias}/download?path=/image.png&inline=true
GET /api/fs/{alias}/download?path=/image.png&width=300&height=200
GET /api/fs/{alias}/download?path=/folder  (auto-zips folder)
```

### Upload (Direct — small files)
```
POST /api/fs/{alias}/upload
Content-Type: multipart/form-data

action=direct
path=/uploads/file.txt
file=<binary>
```

### Upload (Chunked — large files)
```
# 1. Initiate
POST /api/fs/{alias}/upload
{"action":"initiate","filename":"video.mp4","path":"/uploads/video.mp4","total_size":104857600,"checksum_sha256":"abc123..."}

# 2. Upload each chunk
POST /api/fs/{alias}/upload
Content-Type: multipart/form-data
action=chunk&upload_id=upl_xxx&chunk_index=0&chunk_hash=sha256_of_chunk&file=<binary>

# 3. Finalize
POST /api/fs/{alias}/upload
{"action":"finalize","upload_id":"upl_xxx"}
```

### File Actions
```
POST /api/fs/{alias}/action
{
  "action": "rename",   // rename | move | copy | delete | mkdir
  "source": "/old.txt",
  "target": "/new.txt"
}
```

---

## Admin API `/api/admin`

All admin endpoints require a key with `full_admin` set to `true`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/keys` | List all static & dynamic API keys (secrets masked) |
| POST | `/api/admin/keys` | Generate, hash & save a new dynamic API key (returns unrecoverable `secret` once) |
| DELETE | `/api/admin/keys/{name}` | Revoke an API key dynamically (or ban static key) |
| GET | `/api/admin/bans` | List active IP and key bans |
| POST | `/api/admin/bans/ip` | Ban an IP (Persistent SQLite state) |
| DELETE | `/api/admin/bans/ip/{ip}` | Unban an IP |
| POST | `/api/admin/bans/key` | Ban an API key |
| DELETE | `/api/admin/bans/key/{name}` | Unban an API key |
| GET | `/api/admin/circuit-breakers` | View circuit breaker states |
| POST | `/api/admin/circuit-breakers/{key}/reset` | Reset a circuit breaker (Persistent SQLite state) |
| GET | `/api/admin/config` | View live config (secrets redacted) |
| GET | `/api/admin/databases` | Safe view of connected DB instances (secrets/URLs redacted) |
| GET | `/api/admin/webhooks` | Safe view of event hook registrations (secrets redacted) |
| GET | `/api/admin/rate-limits` | View rate limit global and per-key overrides |

---

## Federation API `/api/fed`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/fed/servers` | List federated servers and their status |

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
