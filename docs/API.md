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

curl -X GET "http://localhost:4500/api/db/databases" \
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
curl -X GET "http://localhost:4500/api/spec" \
     -H "Authorization: Bearer <TOKEN>"
```

---

## Database API `/api/db`

### 1. List Databases
```bash
curl -X GET "http://localhost:4500/api/db/databases" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. List Tables
```bash
curl -X GET "http://localhost:4500/api/db/main_db/tables" \
     -H "Authorization: Bearer <TOKEN>"
```

### 3. Execute Raw SQL
> [!CAUTION]
> Raw SQL is validated by AST parser. Dangerous operations blocked per config.

```bash
curl -X POST "http://localhost:4500/api/db/main_db/query" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "sql": "SELECT * FROM users WHERE id = :id",
           "params": {"id": 42}
         }'
```

### 4. Fetch Rows (with filtering)
```bash
curl -G "http://localhost:4500/api/db/main_db/users/rows" \
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
curl -X POST "http://localhost:4500/api/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "rows": [{"name": "Alice", "active": true}]
         }'
```

### 6. Update Rows
```bash
curl -X PUT "http://localhost:4500/api/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "filter": {"id": 42},
           "update": {"active": false}
         }'
```

### 7. Delete Rows
```bash
curl -X DELETE "http://localhost:4500/api/db/main_db/users/rows" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "filter": {"id": 42}
         }'
```

---

## Storage API `/api/fs`

### 1. List Storages
```bash
curl -X GET "http://localhost:4500/api/fs/storages" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. List Folder
```bash
curl -X GET "http://localhost:4500/api/fs/local_fs/list?path=/subfolder" \
     -H "Authorization: Bearer <TOKEN>"
```

### 3. Download File or Folder
```bash
# Inline view of a file
curl -X GET "http://localhost:4500/api/fs/local_fs/download?path=/image.png&inline=true" \
     -H "Authorization: Bearer <TOKEN>" -O

# Download with image resizing
curl -X GET "http://localhost:4500/api/fs/local_fs/download?path=/image.png&width=300&height=200" \
     -H "Authorization: Bearer <TOKEN>" -o thumb.png

# Download folder as ZIP archive automatically
curl -X GET "http://localhost:4500/api/fs/local_fs/download?path=/reports_folder" \
     -H "Authorization: Bearer <TOKEN>" -o reports.zip
```

### 4. Direct Upload (Small Files)
```bash
curl -X POST "http://localhost:4500/api/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -F "action=direct" \
     -F "path=/uploads/file.txt" \
     -F "file=@/path/to/local/file.txt"
```

### 5. Chunked Upload (Large Files)
```bash
# Step 1: Initiate
curl -X POST "http://localhost:4500/api/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action":"initiate", "filename":"video.mp4", "path":"/uploads/video.mp4", "total_size":104857600, "checksum_sha256":"abc123..."}'
# Note the `upload_id` returned

# Step 2: Upload Chunks
curl -X POST "http://localhost:4500/api/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -F "action=chunk" \
     -F "upload_id=upl_xxx" \
     -F "chunk_index=0" \
     -F "chunk_hash=sha256_of_chunk" \
     -F "file=@chunk0.bin"

# Step 3: Finalize
curl -X POST "http://localhost:4500/api/fs/local_fs/upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"action":"finalize", "upload_id":"upl_xxx"}'
```

### 6. File Actions (Rename, Move, Copy, Delete, Mkdir)
```bash
curl -X POST "http://localhost:4500/api/fs/local_fs/action" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "action": "rename",
           "source": "/old.txt",
           "target": "/new.txt"
         }'
```

---

## Admin API `/api/admin`

All admin endpoints require an API Key with `full_admin` set to `true`.

### 1. List API Keys
```bash
curl -X GET "http://localhost:4500/api/admin/keys" \
     -H "Authorization: Bearer <TOKEN>"
```

### 2. Generate New Dynamic API Key
Returns a completely new secret just once.
```bash
curl -X POST "http://localhost:4500/api/admin/keys" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "name": "service_bot_1",
           "mode": "readwrite",
           "db_scope": ["*"],
           "fs_scope": ["public_assets"],
           "rate_limit_override": 1000,
           "full_admin": false
         }'
```

### 3. Revoke/Ban API Key
```bash
curl -X DELETE "http://localhost:4500/api/admin/keys/service_bot_1" \
     -H "Authorization: Bearer <TOKEN>"
```

### 4. List Active Bans
```bash
curl -X GET "http://localhost:4500/api/admin/bans" \
     -H "Authorization: Bearer <TOKEN>"
```

### 5. Ban an IP Address
```bash
curl -X POST "http://localhost:4500/api/admin/bans/ip" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "ip": "192.168.1.100",
           "reason": "Abuse of login endpoints",
           "duration_seconds": 3600
         }'
```

### 6. Unban an IP Address
```bash
curl -X DELETE "http://localhost:4500/api/admin/bans/ip/192.168.1.100" \
     -H "Authorization: Bearer <TOKEN>"
```

### 7. Ban an API Key Manually
```bash
curl -X POST "http://localhost:4500/api/admin/bans/key" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
           "key_name": "legacy_key_2",
           "reason": "Compromised credential",
           "duration_seconds": null
         }'
```

### 8. Unban an API Key
```bash
curl -X DELETE "http://localhost:4500/api/admin/bans/key/legacy_key_2" \
     -H "Authorization: Bearer <TOKEN>"
```

### 9. View Circuit Breaker States
```bash
curl -X GET "http://localhost:4500/api/admin/circuit-breakers" \
     -H "Authorization: Bearer <TOKEN>"
```

### 10. Reset Circuit Breaker Manually
```bash
curl -X POST "http://localhost:4500/api/admin/circuit-breakers/main_db/reset" \
     -H "Authorization: Bearer <TOKEN>"
```

### 11. View Live System Config
```bash
curl -X GET "http://localhost:4500/api/admin/config" \
     -H "Authorization: Bearer <TOKEN>"
```

### 12. List Active Databases
Safe mode without viewing raw connection strings.
```bash
curl -X GET "http://localhost:4500/api/admin/databases" \
     -H "Authorization: Bearer <TOKEN>"
```

### 13. List Webhook Regulations
Safe mode without viewing HMAC Secrets.
```bash
curl -X GET "http://localhost:4500/api/admin/webhooks" \
     -H "Authorization: Bearer <TOKEN>"
```

### 14. View Configuration Rate Limit Overrides
```bash
curl -X GET "http://localhost:4500/api/admin/rate-limits" \
     -H "Authorization: Bearer <TOKEN>"
```

---

## Federation API `/api/fed`

### 1. List Federated Servers
```bash
curl -X GET "http://localhost:4500/api/fed/servers" \
     -H "Authorization: Bearer <TOKEN>"
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
