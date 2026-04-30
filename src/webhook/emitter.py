import asyncio
import base64
import hmac
import time
from typing import Any, Dict, Optional

import structlog
from pydantic import BaseModel

from config.loader import ConfigManager
from utils.uuid7 import uuid7

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Schema Definitions
# ─────────────────────────────────────────────────────────────────────────────


class WebhookTrigger(BaseModel):
    api_key: str
    ip: Optional[str] = None
    request_id: str
    webhook_token: Optional[str] = None


class WebhookEventDetails(BaseModel):
    module: str
    operation: str
    resource: str
    target: str
    action: str
    details: Dict[str, Any]


class WebhookPayload(BaseModel):
    event_id: str
    timestamp: str
    source: str
    event: WebhookEventDetails
    trigger: WebhookTrigger


class WebhookQueueList:
    """Manages the in-memory buffered transmission buffer queue."""

    _queue: Optional[asyncio.Queue] = None

    @classmethod
    def get_queue(cls) -> asyncio.Queue:
        if cls._queue is None:
            config = ConfigManager.get()
            cls._queue = asyncio.Queue(maxsize=config.webhooks.queue_size)
        return cls._queue


# ─────────────────────────────────────────────────────────────────────────────
# Event Engine
# ─────────────────────────────────────────────────────────────────────────────


def _is_token_matched(hook_secret: str, provided_token: Optional[str]) -> bool:
    """Validates the client-provided trigger token cryptographically."""
    if not provided_token:
        return False

    try:
        decoded_token = base64.b64decode(provided_token).decode("utf-8")
        return hmac.compare_digest(hook_secret, decoded_token)
    except Exception:
        return False


def _is_rule_matched(
    hook,
    module: str,
    operation: str,
    resource: str,
    target: str,
    trigger: WebhookTrigger,
) -> bool:
    """Evaluates the dynamic routing rules defined in config against an outbound event."""
    if not hook.enabled:
        return False

    r_mod_op, r_alias_target = hook.rule.split("@")
    r_mod, r_op = r_mod_op.split(".")
    r_alias, r_target = r_alias_target.split(":")

    if r_mod not in ("*", module):
        return False

    if r_op not in ("*", "any", operation):
        return False

    if r_alias not in ("*", resource):
        return False

    if r_target != "*":
        allowed_targets = [t.strip() for t in r_target.split(",")]
        if target not in allowed_targets:
            return False

    return _is_token_matched(hook.secret, trigger.webhook_token)


def _build_payload(
    config,
    module: str,
    operation: str,
    resource: str,
    target: str,
    action: str,
    details: Dict[str, Any],
    trigger: WebhookTrigger,
) -> WebhookPayload:
    """Constructs the heavily structured standard output contract representing the notification."""
    event_details = WebhookEventDetails(
        module=module,
        operation=operation,
        resource=resource,
        target=target,
        action=action,
        details=details,
    )

    return WebhookPayload(
        event_id=f"evt_{uuid7().hex}",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        source=config.server.host,
        event=event_details,
        trigger=trigger,
    )


def emit_event(
    module: str,
    operation: str,
    resource: str,
    target: str,
    action: str,
    details: Dict[str, Any],
    trigger: WebhookTrigger,
) -> None:
    """Entry point invoked by endpoints determining if webhook sync operations should fire."""
    config = ConfigManager.get()
    if not config.features.webhook or not config.webhooks.enabled:
        return

    # Map configured listeners
    matched_hooks = []
    for name, hook in config.webhook.items():
        if _is_rule_matched(hook, module, operation, resource, target, trigger):
            matched_hooks.append((name, hook))

    if not matched_hooks:
        return

    # Pack and distribute
    payload = _build_payload(
        config, module, operation, resource, target, action, details, trigger
    )
    json_payload = payload.model_dump_json()
    queue = WebhookQueueList.get_queue()

    for name, hook in matched_hooks:
        try:
            queue.put_nowait(
                {
                    "hook_name": name,
                    "url": hook.url,
                    "secret": hook.secret,
                    "headers": hook.headers,
                    "payload": json_payload,
                }
            )
        except asyncio.QueueFull:
            logger.error(
                "Webhook queue full, dropping event",
                event_id=payload.event_id,
                hook_name=name,
            )
