# NexusGate Federation Guide

Federation allows multiple NexusGate instances to link together, forming a unified mesh of databases and storage volumes.

## 1. Overview

In a federated setup, a **Primary Gateway** acts as the ingress point. It "mounts" databases and storage volumes from **Remote Nodes** as if they were local resources.

**Key Features:**
- **Unified API**: Access globally distributed data through a single URL.
- **Automatic Prefixing**: Remote resources are automatically prefixed with the node's alias (e.g., `us_west_users_db`).
- **Permission Mapping**: Primary nodes authenticate to remote nodes using a dedicated "Federation Key," while enforcing local permissions for the incoming client.

## 2. Configuration

### On the Remote Node (Region US):
Ensure you have an API key that you will provide to the Primary node.

```toml
[api_key.primary_node_link]
secret = "your_secret_here_base64"
mode = "readwrite"
db_scope = ["*"]
fs_scope = ["*"]
```

### On the Primary Node:
Define the remote server under the `[federation.server.*]` section in `config.toml`.

```toml
[federation]
enabled = true
sync_interval = 60  # Sync remote capabilities every 60s

[federation.server.us_west]
url = "https://us.nexusgate.example.com"
api_key = "primary_node_link_secret"
alias = "us"
trust_mode = "verify"
```

## 3. Resource Mapping

Once synced, resources from the `us_west` node will appear in the Primary node's lists:

- A database named `main_db` on the US server becomes `us_main_db` on the Primary server.
- A storage volume named `uploads` becomes `us_uploads`.

Requests sent to these prefixed names are automatically proxied to the remote server, handled asynchronously, and returned through the Primary node.

## 4. Resilience and Failover

- **Circuit Breaker**: Each federation link is protected by a circuit breaker. If a remote node goes down, the Primary node will "trip" the circuit, returning a `FED_CIRCUIT_OPEN` error immediately rather than waiting for timeouts.
- **Syncing**: The Primary node periodically pings the `/health` and capability endpoints of all federated servers. If a server is marked as `unhealthy`, its resources are temporarily hidden from the Primary node's lists.
- **Timeouts**: Federation requests have their own timeout budget to prevent a slow remote node from exhausting the Primary node's connection pool.

## 5. Security Considerations

- **Encryption**: Always use `https` for federation URLs in production.
- **mTLS**: For maximum security, we recommend terminating TLS at an Nginx/HAProxy layer that enforces mutual TLS between your NexusGate nodes.
- **Rate Limiting**: Remote nodes still apply their own rate limits to the Primary gateway. Ensure the Primary gateway's key on the remote node has a sufficiently high `rate_limit_override`.
