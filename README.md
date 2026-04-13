# NexusGate
An open-source, industrial-grade, unified API gateway for databases and file storage with federated multi-server support, webhook event streaming, and military-grade security.

## Philosophy
- **Ultra-lightweight**: Fast and efficient.
- **Universal Agnostic Target**: One language to speak to MySQL, SQLite, Postgres, and the FileSystem.
- **Secure By Default**: Strict WAF, rate limits, and path traversal blockades.

## Features
- Dynamic connection pooling to any SQL dialect using `sqlglot` for AST verification.
- Virtual file system proxy with built in zip-streaming and image resizing.
- Real-time webhook emissions based on regex-like operation subscriptions.
- Async HTTP streaming reverse-proxies for Federated edge nodes.
- High performance multi-tier rate limiting with DDoS protection.
- Cache-Aside SQLite persistent state layer for sub-millisecond API Key and Ban validations.

## Quick Start
1. `pip install -r requirements.txt`
2. Run via: `python src/main.py`
3. Check the auto-generated `config.toml` for your new Admin API Key.

## Example CURL
```bash
# Query Database
curl -X POST "http://localhost:4500/api/v1/db/main_db/query" \
     -H "Authorization: Bearer <API_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"sql": "SELECT id, name FROM users WHERE active = :status", "params": {"status": true}}'

# Storage Upload Setup
curl -X POST "http://localhost:4500/api/v1/fs/local_fs/upload" \
     -H "Authorization: Bearer <API_KEY>" \
     -d '{"action": "initiate", "filename": "test.png", "total_size": 10240, "path": "/foo/test.png", "checksum_sha256": "..."}'
```
