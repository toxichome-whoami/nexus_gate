# NexusGate Security Model

NexusGate is designed with a "Security-First" philosophy, specifically engineered to withstand industrial-grade API attack vectors.

## 1. Zero-Trust Internal Architecture

- **Scope-Based Access**: Every API key is restricted by `mode` (readonly, writeonly, readwrite), `db_scope` (permitted database aliases), and `fs_scope` (permitted storage aliases).
- **Mode Intersection**: Permissions are calculated by taking the intersection of the API key's mode and the resource's (database or storage) configured mode.
- **Service-Level Isolation**: No cross-service data leakage. Database engines cannot interact with the storage system directly and vice versa.

## 2. Attack Protections

| Threat | Protection Mechanism |
|--------|----------------------|
| **SQL Injection** | Mandatory use of `sqlglot` for AST-based parsing and validation. Parameterized queries are enforced; string interpolation is mathematically impossible in the data layer. |
| **Path Traversal** | Comprehensive `../` and null-byte filtering in the `WAFMiddleware`. All file paths are canonicalized and jailed within the storage volume root. |
| **Brute Force** | Multi-tier sliding-window rate limiting (Global, Per-Key, Per-IP) with a penality cooldown that bans repeated violators. |
| **Timing Attacks** | All authentication secret comparisons use `hmac.compare_digest` (constant-time comparison). |
| **MIME Sniffing** | All responses include `X-Content-Type-Options: nosniff`. |
| **XSS** | Strict `application/json` content-type enforcement and WAF-based input sanitization. |
| **Clickjacking** | `X-Frame-Options: DENY` is added to all responses by the `SecurityHeadersMiddleware`. |

## 3. Web Application Firewall (WAF)

NexusGate includes an embedded WAF layer (`src/server/middleware/waf.py`) that executes before any business logic.

- **Request Size Limiting**: Rejects requests that exceed `server.body_limit`.
- **Content-Type Enforcement**: Rejects unexpected content types (e.g., enforces JSON for API calls).
- **Input Sanitization**: Automatically removes null bytes and suspicious Unicode character sequences.
- **Pattern Matching**: Blocks requests containing common exploit strings in URLs and headers.

## 4. Idempotency

Mutating requests (`POST`, `PUT`, `DELETE`) can be made idempotent by providing a `X-Idempotency-Key` header.
- The gateway caches the result of the first successful execution for 24 hours.
- Subsequent requests with the same key receive the cached response without re-executing the operation.
- This prevents duplicate database records and file operations in the event of network retry loops.

## 5. Security Recommendations for Production

- **TLS/SSL**: Always set `tls_cert` and `tls_key` in `config.toml` or terminate TLS at a trusted reverse proxy (e.g., Nginx, Cloudflare).
- **Restricted Scoping**: Never use `["*"]` for `db_scope` or `fs_scope` on keys exposed to end-user applications.
- **Redaction**: Avoid enabling `features.playground` in public production environments.
- **Log Rotation**: Ensure `logging.directory` is on a partition with sufficient space to prevent service denial due to disk exhaustion.
- **IP Ban Persistence**: Use a Redis backend for rate limiting if you require ban persistence across container restarts.
