# NexusGate Federation Guide

Federation allows multiple NexusGate instances to link together, forming a unified mesh of databases and storage volumes.

## 1. Overview

In a federated setup, a **Connector Server** (Client) proxies requests to a **Receiver Server** (Host). The Connector can query databases and storage on the Receiver as if they were local resources.

**Key Features:**
- **Unified API**: Access globally distributed data through a single URL.
- **Map-Reduce Data Mesh**: Query a comma-separated list of nodes (`alias_a,alias_b`) to concurrently fetch and join remote JSON data entirely in-memory using `asyncio.gather`.
- **Automatic Prefixing**: Remote resources are auto-prefixed with the node alias (e.g., `us_west_main_db`).
- **Isolated Auth**: Federation uses its own dedicated secrets — completely separate from API keys.
- **Per-Node Scoping**: Each incoming node has its own `mode`, `db_scope`, and `fs_scope`.
- **One-Way by Default**: Server A connecting to Server B does NOT give Server B access to Server A. For two-way access, both servers must configure each other.

## 2. Configuration

### On the Receiver Server (Server B — the one being connected TO):

Define incoming keys for each remote node that should be allowed to connect.

```toml
[features]
federation = true

[federation]
enabled = true
sync_interval = 30

# Each block = ONE remote node. Create more blocks for more servers.
[federation.incoming.us_east_node]
secret = "gK8xPmW2qR7nY4vB9cT1jL6hF3dA0sE"   # Min 32 chars, unique per node
mode = "readwrite"                              # readonly | readwrite
db_scope = ["*"]                                # ["*"] = all, or ["main_db"]
fs_scope = ["*"]                                # ["*"] = all, or ["uploads"]
description = "US East production node"

# Example: a second node with restricted access
[federation.incoming.eu_analytics]
secret = "Xw9Lp2Kd7Rm4Yn6Bv3Ct1Jh8Gf5As0Eq"
mode = "readonly"
db_scope = ["analytics"]
fs_scope = []
description = "EU analytics readonly mirror"
```

### On the Connector Server (Server A — the one that connects):

Define outgoing connections to the remote server.

```toml
[features]
federation = true

[federation]
enabled = true
sync_interval = 30

[federation.server.node_b]
url = "https://server-b.example.com"
secret = "gK8xPmW2qR7nY4vB9cT1jL6hF3dA0sE"    # Must match Server B's incoming key
node_id = "us_east_node"                         # Your identity on Server B
trust_mode = "verify"                            # verify | trust (skip TLS check)
```

> **Important:** The `secret` on Server A must be the **exact same string** as the `secret` on Server B's `[federation.incoming.us_east_node]` block. The `node_id` must match the incoming block name.

## 3. Authentication Flow

Federation secrets are **completely separate** from API keys. They use dedicated headers:

1. **Server A** sends a request with:
   - `X-Federation-Secret`: Base64-encoded federation secret
   - `X-Federation-Node`: The node identity (e.g., `us_east_node`)

2. **Server B** receives the request:
   - Looks up the node in `federation.incoming`
   - Base64-decodes the secret
   - Compares using `hmac.compare_digest()` (constant-time, timing-attack safe)
   - If valid, creates a scoped `AuthContext` with the incoming key's permissions
   - Federation keys can **never** have `full_admin` access

3. **Config stores plain text**, transport uses Base64 — handled automatically.

## 4. Resource Mapping

Once synced, remote resources appear with a prefix:

- A database `main_db` on Server B becomes `node_b_main_db` on Server A.
- A storage volume `uploads` becomes `node_b_uploads`.

Requests to prefixed names are automatically proxied to the remote server.

## 5. Monitoring

Call `GET /api/federation/servers` (requires `full_admin` API key) to see:

```json
{
  "outgoing": [
    { "alias": "node_b", "url": "...", "node_id": "us_east_node", "status": "up", "latency_ms": 45 }
  ],
  "outgoing_count": 1,
  "incoming": [
    { "node_id": "us_east_node", "mode": "readwrite", "db_scope": ["*"], "description": "US East" }
  ],
  "incoming_count": 1
}
```

- **outgoing**: Servers this node connects TO (with live health status)
- **incoming**: Servers allowed to connect TO this node (with their permissions)
- Secrets are **never** exposed in responses

## 6. Resilience

- **Circuit Breaker**: Each federation link is protected. If a remote node goes down, it returns `FED_CIRCUIT_OPEN` immediately instead of waiting for timeouts.
- **Health Syncing**: The connector periodically pings remote servers. Unhealthy servers have their resources temporarily hidden.
- **Timeouts**: Federation requests have their own timeout to prevent slow remotes from exhausting connection pools.

## 7. Security

- **Isolated Secrets**: Federation secrets cannot be used as API keys, and vice versa.
- **Per-Node Scoping**: Each node has independent `mode`, `db_scope`, and `fs_scope`.
- **Key Compromise**: If one node is compromised, delete its `[federation.incoming.*]` block and restart. Only that node loses access.
- **No Admin Access**: Federation keys always have `full_admin=false` — they cannot access `/api/admin/*` or `/api/federation/servers`.
- **Encryption**: Always use `https` for federation URLs in production.
