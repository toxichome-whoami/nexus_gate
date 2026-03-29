"""Integration tests for webhook emitter and queue behavior."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def test_webhook_emit_no_config(test_client):
    """Webhooks should silently drop events when no rules match."""
    from webhook.emitter import emit_event, WebhookTrigger

    # Should not raise even without matching webhook rules
    emit_event(
        module="db",
        operation="write",
        resource="main_db",
        target="users",
        action="INSERT",
        details={"affected": 1},
        trigger=WebhookTrigger(api_key="test", ip="127.0.0.1", request_id="req-001"),
    )


def test_webhook_signer_correct_format():
    from webhook.signer import generate_signature

    sig = generate_signature("my_secret_key_here", '{"event": "test"}')
    assert sig.startswith("sha256=")
    assert len(sig) == 71  # "sha256=" (7) + 64 hex chars


def test_webhook_signer_deterministic():
    from webhook.signer import generate_signature

    payload = '{"data": "hello"}'
    secret = "deterministic_secret_32_chars_min"
    sig1 = generate_signature(secret, payload)
    sig2 = generate_signature(secret, payload)
    assert sig1 == sig2


def test_webhook_signer_different_secrets_differ():
    from webhook.signer import generate_signature

    payload = '{"data": "hello"}'
    sig1 = generate_signature("secret_one_32_chars_min_padding00", payload)
    sig2 = generate_signature("secret_two_32_chars_min_padding00", payload)
    assert sig1 != sig2


@pytest.mark.asyncio
async def test_queue_full_drops_gracefully():
    """When the queue is at max capacity, events should be dropped without exception."""
    from webhook.emitter import WebhookQueueList

    # Reset singleton queue to a tiny queue
    WebhookQueueList._queue = asyncio.Queue(maxsize=1)
    WebhookQueueList._queue.put_nowait({"test": True})  # Fill it

    from webhook.emitter import emit_event, WebhookTrigger
    from config.loader import ConfigManager

    # Patch config to enable webhooks and have a matching rule
    with patch.object(ConfigManager, "get") as mock_cfg:
        cfg = MagicMock()
        cfg.features.webhook = True
        cfg.webhooks.enabled = True
        cfg.webhook = {}
        mock_cfg.return_value = cfg

        # This should not raise even though queue is full
        emit_event(
            "db", "write", "main_db", "users", "INSERT", {},
            WebhookTrigger(api_key="admin", ip="127.0.0.1", request_id="r1"),
        )

    # Clean up
    WebhookQueueList._queue = None
