# NexusGate Architecture

NexusGate is built on an aggressive, fully async architecture utilizing `FastAPI`, `httpx`, and `SQLGlot`.

## Security Subsystem (Cache-Aside)
NexusGate uses an embedded SQLite database (`data/security.db`) coupled with a native Python `dict` caching layer. This provides zero-latency (sub-nanosecond) authentication and ban evaluations without sacrificing disk-based persistence for dynamic API keys or circuit breaker thresholds.

## The Pipeline

> **Performance Note:** All middleware operates at the raw ASGI protocol level for maximum performance. No middleware uses Starlette's `BaseHTTPMiddleware`, eliminating threadpool overhead on every request.

Every incoming request flows through the following middleware sequence:
1. **SecurityHeadersMiddleware**: Injects HSTS, CSP, X-Content-Type, Cache-Control, and X-Frame-Options headers. Header sets (API, docs, base) are **pre-computed as immutable tuples at import time** — zero allocation cost per response. Includes relaxed CSP for documentation endpoints (`/docs`, `/redoc`).
2. **RequestIDMiddleware**: Tags every request with a UUIDv7 timestamp-sorted ID.
3. **WAFMiddleware**: Pre-filters oversized payloads, null-byte injections, and standard Path Traversal patterns before the router even sees the data.
4. **LogMiddleware**: Attaches the request context to `structlog` for structured, parseable JSON logs.
5. **RateLimitMiddleware**: An IP + API Key fixed-window cache enforcing max tokens. The cache backend class and all rate-limit config values (window, burst, penalty) are **resolved once at startup** and stored on the middleware instance — eliminating config lookups from the hot path. The in-memory rate limit counter uses an **O(1) flat counter+expiry pattern** per IP, guaranteeing constant RAM usage regardless of request volume.

## Handlers
The pipeline converges at the Router which redirects to:
- **Database (`/api/db`)**: Requests are intercepted by the `QueryExecutionPipeline` and `QueryValidator` classes. The `QueryValidator` employs a deterministic LRU cache to drastically reduce `sqlglot` AST parsing overhead. It walks the AST tree, validates user permissions against statement types, and transpiles syntax dynamically.
- **Storage (`/api/fs`)**: Executes zero-copy `aiofiles` streamed proxies and implements active `CircuitBreaker` integrations to block bandwidth saturation. All large file uploads via the `ChunkedUploadManager` use direct socket-to-disk 64KB streams (`write_chunk_stream`) with on-the-fly cryptographic hashing, rendering the gateway fully immune to Out-of-Memory (OOM) crashes during massive concurrent uploads.

## Webhook Queue
Write events (Uploads, Inserts, Updates, Deletes) are dropped into a non-blocking `asyncio.Queue` via `emitter.py`. The `dispatcher.py` background worker strips items from the queue, calculates `HMAC-SHA256` payload signatures using the target's secret, and attempts delivery via HTTP POST, implementing exponential backoff retries on failure.

## Federation
Remote NexusGate instances can be configured in `config.toml`. Through `/api/fed/*`, identical structural requests mapped to `alias` are routed via `StreamingResponse` HTTPX clients, bridging queries between geographically isolated servers seamlessly.

### HTTP Connection Pool Lifecycle
Federation proxy clients (`httpx.AsyncClient`) are **attached to `app.state.http_clients`** rather than a module-level global. This ensures:
- Connection pools are cleanly initialized on first use and shared across all requests.
- On server shutdown, the `lifespan` context manager iterates over all clients in `app.state.http_clients` and calls `aclose()` — preventing socket leaks on restart.
- Horizontal scaling is safe: each process owns its own isolated pool in its own ASGI app state.
