# NexusGate Architecture

NexusGate is built on an aggressive, fully async architecture utilizing `FastAPI`, `httpx`, and `SQLGlot`.

## Security Subsystem (Cache-Aside)
NexusGate uses an embedded SQLite database (`data/security.db`) coupled with a native Python `dict` caching layer. This provides zero-latency (sub-nanosecond) authentication and ban evaluations without sacrificing disk-based persistence for dynamic API keys or circuit breaker thresholds.

## The Pipeline

> **Performance Note:** All middleware operates at the raw ASGI protocol level for maximum performance. No middleware uses Starlette's `BaseHTTPMiddleware`, eliminating threadpool overhead on every request.

Every incoming request flows through the following middleware sequence:
1. **SecurityHeadersMiddleware**: Injects HSTS, CSP, X-Content-Type, Cache-Control, and X-Frame-Options headers. Includes dynamic CSP bypasses for documentation endpoints (`/docs`, `/redoc`).
2. **RequestIDMiddleware**: Tags every request with a UUIDv7 timestamp-sorted ID.
3. **WAFMiddleware**: Pre-filters oversized payloads, null-byte injections, and standard Path Traversal patterns before the router even sees the data.
4. **LogMiddleware**: Attaches the request context to `structlog` for structured, parseable JSON logs.
5. **RateLimitMiddleware**: An IP + API Key sliding window cache enforcing max tokens.

## Handlers
The pipeline converges at the Router which redirects to:
- **Database (`/api/db`)**: Where SQLGlot intercepts dynamic JSON-to-SQL logic, walks the AST tree, validates user permissions against statement types (e.g. read-only cannot INSERT), and transpiles syntax dynamically to Postgres/MySQL/SQLite driver pools.
- **Storage (`/api/fs`)**: Executes zero-copy aiofiles streamed proxies, intercepts chunk assemblies, processes image thumbnails via Pillow, and supports enterprise operations (info, exists, bulk_delete, bulk_move) before flushing the response chunk-by-chunk.

## Webhook Queue
Write events (Uploads, Inserts, Updates, Deletes) are dropped into a non-blocking `asyncio.Queue` via `emitter.py`. The `dispatcher.py` background worker strips items from the queue, calculates `HMAC-SHA256` payload signatures using the target's secret, and attempts delivery via HTTP POST, implementing exponential backoff retries on failure.

## Federation
Remote NexusGate instances can be configured in `config.toml`. Through `/api/fed/*`, identical structural requests mapped to `alias` are routed via `StreamingResponse` HTTPX clients, bridging queries between geographically isolated servers seamlessly.
