"""Integration tests for the asynchronous webhook dispatch and cryptographic signing layers."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from webhook.emitter import emit_event, WebhookTrigger, WebhookQueueList
from webhook.signer import generate_signature
from config.loader import ConfigManager

# ─────────────────────────────────────────────────────────────────────────────
# Static Logic & Signer Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_webhook_event_omission_without_active_configuration():
    """Ensure that the emitter is a non-blocking no-op when no rules match."""
    # This execution should not raise or block even without matching webhook definitions
    emit_event(
        module="db",
        operation="write",
        resource="mock_database",
        target="users",
        action="INSERT",
        details={"rows": 1},
        trigger=WebhookTrigger(api_key="internal_test", ip="127.0.0.1", request_id="id-001"),
    )

def test_webhook_signature_format_validity():
    """Verify the structural integrity of the generated HMAC signature."""
    signature = generate_signature("my_signing_secret_key", '{"event": "ping"}')
    
    assert signature.startswith("sha256=")
    # 7 characters for "sha256=" + 64 hex characters for the hash
    assert len(signature) == 71

def test_webhook_signature_is_deterministic():
    """Ensure that identical payloads and secrets produce identical hashes."""
    secret, payload = "shared_secret_32_chars_padding_00", '{"data": "fixed"}'
    
    sig_alpha = generate_signature(secret, payload)
    sig_omega = generate_signature(secret, payload)
    
    assert sig_alpha == sig_omega

# ─────────────────────────────────────────────────────────────────────────────
# Queue & Backpressure Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_emitter_drops_events_on_queue_saturation():
    """Ensure system stability when the background event queue reaches peak capacity."""
    
    # Setup: Temporarily restrict the singleton queue to exactly 1 slot
    WebhookQueueList._queue = asyncio.Queue(maxsize=1)
    # Fill the slot to trigger saturation
    WebhookQueueList._queue.put_nowait({"saturated": True})

    # Mock configuration to simulate an enabled webhook feature
    with patch.object(ConfigManager, "get") as config_mock:
        mocked_cfg = MagicMock()
        mocked_cfg.features.webhook = True
        mocked_cfg.webhooks.enabled = True
        mocked_cfg.webhook = {}
        config_mock.return_value = mocked_cfg

        try:
            # Step: Attempt emission - this must drop the event silently rather than crashing
            emit_event(
                "fs", "write", "storage_node", "files", "UPLOAD", {},
                WebhookTrigger(api_key="admin_user", ip="127.0.0.1", request_id="rid-1"),
            )
        finally:
            # Teardown: Restore the queue to a default state
            WebhookQueueList._queue = None
