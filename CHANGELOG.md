# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2024-03-28

### Added
- **Core API Gateway**: Unified routing for multiple database and storage backends.
- **Multi-Database Support**: Native drivers for PostgreSQL, MySQL, SQLite, and MSSQL with connection pooling.
- **SQL Transpiler**: AST-based query validation and dialect translation using `sqlglot`.
- **Virtual File System**: Secure proxy for local storage with path jail and extension filtering.
- **Chunked Resumable Uploads**: Industrial-grade large file upload protocol with SHA-256 integrity verification.
- **Dynamic Image Processing**: On-the-fly resizing, quality adjustment, and format conversion for storage assets.
- **Security Middleware Suite**: 
    - `RateLimitMiddleware`: Multi-tier sliding-window limiting (IP & Key).
    - `WAFMiddleware`: Built-in Web Application Firewall for path traversal and input sanitization.
    - `IdempotencyMiddleware`: Prevention of duplicate operations via `X-Idempotency-Key`.
    - `SecurityHeadersMiddleware`: Automatic injection of production-best-practice headers.
- **Webhook Engine**: Asynchronous event streaming with rule-based matching and HMAC-SHA256 signing.
- **Admin API**: Comprehensive management of API keys, IP/Key bans, and circuit breaker states.
- **Observability**: Prometheus-compatible `/metrics` endpoint and structured JSON logging.
- **Federation**: Capability to mesh multiple NexusGate instances together.
- **CLI Tooling**: Command-line arguments for config validation, key generation, and version checking.
- **Production Documentation**: Full suite of guides in `docs/` covering security, config, and deployment.
