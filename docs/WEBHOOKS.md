# NexusGate Webhooks Guide

NexusGate features a high-performance event streaming system that can notify external applications of any database or file system activity in real-time.

## 1. Event Flow

1.  **Trigger**: An operation (INSERT, UPDATE, DELETE, UPLOAD, etc.) is successfully committed.
2.  **Match**: The event is matched against the `rule` patterns defined in `config.toml`.
3.  **Queue**: If a match is found, the event is placed in a non-blocking internal queue.
4.  **Delivery**: A background worker picks up the event and sends it to the configured `url` via HTTP POST.

## 2. Configuration

Webhooks are defined as individual sections in `config.toml`.

```toml
[webhook.new_user_event]
url = "https://your-app.com/api/hooks/new-user"
secret = "hmac_signing_secret_here"
rule = "db.write@main_db:users"
enabled = true
```

### Rule Syntax: `{module}.{operation}@{alias}:{target}`

- **module**: `db` or `fs`
- **operation**: `read`, `write`, `delete`, `any`, or `*`
- **alias**: The name of the database or storage volume (or `*` for all).
- **target**: The table name or file path pattern (or `*` for all).

## 3. Payload Format

All webhooks are sent as JSON with a `X-NexusGate-Signature` header.

```json
{
  "event_id": "evt_01HPC9...",
  "timestamp": "2024-03-28T15:00:00Z",
  "source": "nexusgate-local",
  "event": {
    "module": "db",
    "operation": "write",
    "resource": "main_db",
    "target": "users",
    "action": "INSERT",
    "details": {
      "affected_rows": 1,
      "data": [
        { "id": 123, "email": "alice@example.com" }
      ]
    }
  },
  "trigger": {
    "api_key": "admin",
    "ip": "127.0.0.1",
    "request_id": "req_8899..."
  }
}
```

## 4. Signing and Verification

To ensure that a webhook was actually sent by NexusGate, you **must** verify the HMAC-SHA256 signature provided in the `X-NexusGate-Signature` header.

**Verification Algorithm (Node.js example):**

```javascript
const crypto = require('crypto');

function verifyWebhook(payload, signature, secret) {
    const expected = crypto
        .createHmac('sha256', secret)
        .update(JSON.stringify(payload))
        .digest('hex');
    
    return crypto.timingSafeEqual(
        Buffer.from(`sha256=${expected}`),
        Buffer.from(signature)
    );
}
```

## 5. Reliability and Retries

If a webhook delivery fails (non-2xx response or timeout):
- **Retries**: NexusGate will retry up to `max_retries` (default: 3) times.
- **Exponential Backoff**: We wait `retry_delay ^ attempt` seconds between retries.
- **Queue Buffering**: If the system is under heavy load, events are buffered in memory up to `queue_size`. Beyond this, new events are dropped to prevent memory exhaustion (monitored via `/metrics`).
