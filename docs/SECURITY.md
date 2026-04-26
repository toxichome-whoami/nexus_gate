# NexusGate Security Model

NexusGate is designed with a "Security-First" philosophy, specifically engineered to withstand industrial-grade API attack vectors.

## 1. Zero-Trust Internal Architecture

- **Scope-Based Access**: Every API key is restricted by `mode` (readonly, writeonly, readwrite), `db_scope` (permitted database aliases), and `fs_scope` (permitted storage aliases).
- **Mode Intersection**: Permissions are calculated by taking the intersection of the API key's mode and the resource's (database or storage) configured mode.
- **Service-Level Isolation**: No cross-service data leakage. Database engines cannot interact with the storage system directly and vice versa.

## 2. Three Isolated Authentication Paths

NexusGate enforces strict separation between authentication domains. A credential from one domain **cannot** be used in another.

| Auth Path | Header | Config Location | Transport | Can be Admin? |
|-----------|--------|-----------------|-----------|---------------|
| **API Keys** | `Authorization: Bearer base64(name:secret)` | `[api_key.*]` | Base64 | ✅ Yes (`full_admin`) |
| **Federation** | `X-Federation-Secret` + `X-Federation-Node` | `[federation.incoming.*]` | Base64 | ❌ Never |
| **Webhooks** | `X-NexusGate-Webhook-Token: base64(secret)` | `[webhook.*]` | Base64 | N/A |

- **API Keys** authenticate human users and external applications.
- **Federation Secrets** authenticate server-to-server mesh connections. Each node has independent scope.
- **Webhook Tokens** authorize webhook event emission. Verified via constant-time comparison.

All three paths use Base64 encoding for transport, but the raw secrets are stored as plain text in `config.toml`.

## 3. Dynamic Secret Storage (Cache-Aside Pattern)

- **Persistent Security State**: All API keys generated via the Admin API, along with Administrator-enforced IP and Key bans, are stored in a persistent SQLite database (`data/security.db`).
- **Hashed Secrets**: API Key secrets are never stored in plaintext inside the database. They are hashed using SHA-256 before storage. Even if the database file is exfiltrated, the raw secrets cannot be recovered.
- **Ultra-Low Latency Caching**: To prevent database disk-I/O from creating a bottleneck during DDoS attacks, the SQLite database state is synchronized into a nanosecond-latency RAM cache. Authentication and ban checks occur strictly in memory.

## 4. Attack Protections

| Threat | Protection Mechanism |
|--------|----------------------|
| **SQL Injection** | Mandatory use of `sqlglot` for AST-based parsing and validation. Parameterized queries are enforced; string interpolation is mathematically impossible in the data layer. |
| **Path Traversal** | Comprehensive `../` and null-byte filtering in the `WAFMiddleware`. All file paths are canonicalized and jailed within the storage volume root. |
| **Brute Force** | Multi-tier sliding-window rate limiting (Global, Per-Key, Per-IP) with a penalty cooldown that bans repeated violators. |
| **Timing Attacks** | All secret comparisons (API keys, federation secrets, webhook tokens) use `hmac.compare_digest` (constant-time). |
| **SSRF (Server-Side Request Forgery)** | The Federation proxy employs a strict Bogon/Localhost IP validator to ensure outbound network connections cannot be manipulated into routing to internal AWS metadata IPs or local resources. |
| **Denial of Service (DoS)** | Protected via `CircuitBreaker` states linked to large Storage stream outputs. Additionally, all chunked file uploads bypass RAM by writing directly to disk via streaming sockets, neutralizing Out-Of-Memory (OOM) memory exhaustion attacks. |
| **MIME Sniffing** | All responses include `X-Content-Type-Options: nosniff`. |
| **XSS** | Strict `application/json` content-type enforcement and WAF-based input sanitization. |
| **Clickjacking** | `X-Frame-Options: DENY` is added to all responses by the unified `SecurityHeadersMiddleware` (pure ASGI). |

> [!NOTE]
> The `info` and `exists` storage actions are available to read-only API keys. All other mutating storage and database actions enforce `readwrite` or `writeonly` mode.

## 5. Web Application Firewall (WAF)

NexusGate includes an embedded WAF layer (`src/server/middleware/waf.py`) that executes before any business logic.

- **Request Size Limiting**: Rejects requests that exceed `server.body_limit`.
- **Content-Type Enforcement**: Rejects unexpected content types (e.g., enforces JSON for API calls).
- **Input Sanitization**: Automatically removes null bytes and suspicious Unicode character sequences.
- **Pattern Matching**: Blocks requests containing common exploit strings in URLs and headers.

## 6. Idempotency

Mutating requests (`POST`, `PUT`, `DELETE`) can be made idempotent by providing a `X-Idempotency-Key` header.
- The gateway caches the result of the first successful execution for 24 hours.
- Subsequent requests with the same key receive the cached response without re-executing the operation.
- This prevents duplicate database records and file operations in the event of network retry loops.

## 7. Security Recommendations for Production

- **TLS/SSL**: Always set `tls_cert` and `tls_key` in `config.toml` or terminate TLS at a trusted reverse proxy (e.g., Nginx, Cloudflare).
- **Restricted Scoping**: Never use `["*"]` for `db_scope` or `fs_scope` on keys exposed to end-user applications.
- **Federation Scoping**: Give federated nodes the minimum permissions needed. Use `readonly` mode and restrict `db_scope` where possible.
- **Unique Secrets**: Never reuse the same secret across different federation nodes or webhooks.
- **Redaction**: Avoid enabling `features.playground` in public production environments.
- **Log Rotation**: Ensure `logging.directory` is on a partition with sufficient space to prevent service denial due to disk exhaustion.
- **Rate Limit Penalties**: While admin-issued bans are permanently stored in SQLite, temporary IP penalties issued automatically by the rate limiter use the cache backend. Use a Redis backend for rate limiting if you require penalty persistence across load-balanced workers or container restarts.

