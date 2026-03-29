# NexusGate Webhooks Guide

NexusGate features a high-performance event streaming system that can notify external applications of any database or file system activity in real-time.

## 1. Event Flow

1.  **Trigger**: An operation (INSERT, UPDATE, DELETE, UPLOAD, etc.) is successfully committed.
2.  **Auth**: The request must include the webhook secret as a Base64-encoded token in the `X-NexusGate-Webhook-Token` header.
3.  **Match**: The event is matched against the `rule` patterns defined in `config.toml`, and the decoded token is verified against the webhook's stored secret.
4.  **Queue**: If matched and verified, the event is placed in a non-blocking internal queue.
5.  **Delivery**: A background worker picks up the event and sends it to the configured `url` via HTTP POST, signed with HMAC-SHA256.

## 2. Configuration

Webhooks are defined as individual sections in `config.toml`.

```toml
[webhook.new_user_event]
url = "https://your-app.com/api/hooks/new-user"
secret = "hmac_signing_secret_here_at_least_32_chars"
rule = "db.write@main_db:users"
enabled = true
```

### Rule Syntax: `{module}.{operation}@{alias}:{target}`

- **module**: `db` or `fs`
- **operation**: `read`, `write`, `delete`, `any`, or `*`
- **alias**: The name of the database or storage volume (or `*` for all).
- **target**: The table name or file path pattern (or `*` for all).

## 3. Authentication (Client → Gateway)

When sending requests to the gateway that should trigger webhooks, the client **must** include the webhook secret as a Base64-encoded token:

```
X-NexusGate-Webhook-Token: base64(your_webhook_secret)
```

**Node.js example:**
```javascript
const webhookToken = Buffer.from('your_webhook_secret').toString('base64');

headers: {
    'Authorization': `Bearer ${apiKeyToken}`,
    'X-NexusGate-Webhook-Token': webhookToken,
}
```

The gateway decodes the Base64 token and verifies it against the stored secret using constant-time comparison (`hmac.compare_digest`). If the token doesn't match, the webhook will not fire.

## 4. Payload Format (Gateway → Your App)

All webhooks are delivered as JSON POST requests with signature headers.

**Headers sent by NexusGate:**
| Header | Description |
|--------|-------------|
| `X-NexusGate-Signature` | `sha256=<HMAC-SHA256 hex digest>` |
| `X-NexusGate-Timestamp` | Unix timestamp of delivery |

**Payload body:**
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
    "ip": "103.137.7.105",
    "request_id": "req_8899..."
  }
}
```

## 5. Signature Verification (Your App)

To ensure a webhook was actually sent by NexusGate, **verify the HMAC-SHA256 signature**.

**Node.js example:**
```javascript
const crypto = require('crypto');

function verifyWebhook(rawBody, signatureHeader, secret) {
    const expected = crypto
        .createHmac('sha256', secret)
        .update(rawBody)
        .digest('hex');
    
    return crypto.timingSafeEqual(
        Buffer.from(`sha256=${expected}`),
        Buffer.from(signatureHeader)
    );
}
```

## 6. Security Model

| Layer | Mechanism |
|-------|-----------|
| **Client → Gateway** | Webhook secret sent as Base64 in `X-NexusGate-Webhook-Token` header |
| **Gateway verification** | Decodes Base64, compares with `hmac.compare_digest()` (timing-safe) |
| **Gateway → Your App** | Payload signed with `HMAC-SHA256(secret, body)` |
| **Your App verification** | Recomputes HMAC and compares signatures |

> **Note:** The raw secret is **never** sent in the outgoing webhook delivery. Only the HMAC signature is transmitted.

## 7. Reliability and Retries

If a webhook delivery fails (non-2xx response or timeout):
- **Retries**: NexusGate will retry up to `max_retries` (default: 3) times.
- **Exponential Backoff**: Wait `retry_delay ^ attempt` seconds between retries.
- **Queue Buffering**: Events are buffered in memory up to `queue_size`. Beyond this, new events are dropped to prevent memory exhaustion.
