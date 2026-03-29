# NexusGate Deployment Guide

## Requirements

- Python 3.11+
- Optional: Redis (for distributed rate limiting and caching)
- Optional: Docker + Docker Compose

---

## Quick Start (Local)

```bash
# 1. Clone the repo
git clone https://github.com/yourorg/nexusgate
cd nexusgate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp config.example.toml config.toml
# Edit config.toml — update secrets, database URLs, storage paths

# 4. Run
python src/main.py
# Server starts at http://0.0.0.0:4500
# Admin API key will be printed to stdout on first run
```

---

## Docker (Recommended)

```bash
# Build the image
docker build -t nexusgate:latest .

# Run with a local config
docker run -d \
  -p 4500:4500 \
  -v $(pwd)/config.toml:/config.toml \
  -v $(pwd)/storage:/storage \
  -v $(pwd)/logs:/logs \
  -v $(pwd)/data:/data \
  nexusgate:latest
```

### Docker Compose (with Redis)

```bash
cp config.example.toml config.toml
# Set cache.backend = "redis" and cache.redis_url = "redis://redis:6379/0" in config.toml

docker compose up -d
```

---

## Production Checklist

- [ ] Replace all `CHANGE_ME` secrets in `config.toml` with cryptographically random values (>= 64 chars)
- [ ] Set `server.cors_origins` to your actual frontend domain(s)
- [ ] Enable TLS by setting `tls_cert` and `tls_key` (or terminate TLS at your reverse proxy)
- [ ] Set `features.playground = false` to disable Swagger UI
- [ ] Configure `rate_limit.max_requests` appropriate for your expected traffic
- [ ] Set `cache.backend = "redis"` and `rate_limit.backend = "redis"` for multi-worker deployments
- [ ] Ensure the `/data` directory is mounted to a persistent volume, as it stores dynamic API keys and security state in SQLite
- [ ] Configure `logging.level = "WARN"` or `"ERROR"` for production
- [ ] Set `storage.<alias>.blocked_extensions` to block potentially dangerous uploads
- [ ] Review `database.<alias>.dangerous_operations = false` (default) to prevent DDL

---

## Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl;
    server_name api.example.com;

    ssl_certificate /etc/ssl/certs/api.crt;
    ssl_certificate_key /etc/ssl/private/api.key;

    # Increase buffer for large file uploads
    client_max_body_size 500m;

    location / {
        proxy_pass http://127.0.0.1:4500;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # For streaming downloads
        proxy_buffering off;
        proxy_cache off;
    }
}
```

---

## Systemd Service

```ini
[Unit]
Description=NexusGate API Gateway
After=network.target

[Service]
Type=simple
User=nexusgate
WorkingDirectory=/opt/nexusgate
ExecStart=/opt/nexusgate/.venv/bin/python src/main.py --config /etc/nexusgate/config.toml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable nexusgate
sudo systemctl start nexusgate
```

---

## Monitoring

NexusGate exposes OpenMetrics at `/metrics` (requires `features.metrics = true`).

**Prometheus scrape config:**
```yaml
scrape_configs:
  - job_name: nexusgate
    static_configs:
      - targets: ["localhost:4500"]
    metrics_path: /metrics
    bearer_token: "<base64(admin:secret)>"
```

**Key metrics to alert on:**
- `nexusgate_memory_mb` > 450 MB (approaching limit)
- `nexusgate_db_query_errors_total` increasing rate
- `nexusgate_rate_limit_hits_total` spike (potential attack)
- `nexusgate_webhook_failed_total` increasing (delivery issues)

---

## Upgrading

```bash
# Pull latest
git pull origin main

# Update deps
pip install -r requirements.txt

# Restart
sudo systemctl restart nexusgate
```

> [!NOTE]
> Check the CHANGELOG for breaking config changes before upgrading.
