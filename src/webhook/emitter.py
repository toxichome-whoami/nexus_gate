import time
import asyncio
import base64
import hmac
import structlog
from typing import Dict, Any, Optional
from pydantic import BaseModel

from config.loader import ConfigManager
from utils.uuid7 import uuid7

logger = structlog.get_logger()

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
    _queue: Optional[asyncio.Queue] = None

    @classmethod
    def get_queue(cls) -> asyncio.Queue:
        if cls._queue is None:
            config = ConfigManager.get()
            cls._queue = asyncio.Queue(maxsize=config.webhooks.queue_size)
        return cls._queue

def emit_event(
    module: str,
    operation: str,
    resource: str,
    target: str,
    action: str,
    details: Dict[str, Any],
    trigger: WebhookTrigger
) -> None:
    config = ConfigManager.get()
    if not config.features.webhook or not config.webhooks.enabled:
        return

    rules_matched = []

    # Simple rule matcher: "module.operation@alias:target"
    for name, hook in config.webhook.items():
        if not hook.enabled:
            continue

        r_mod_op, r_alias_target = hook.rule.split("@")
        r_mod, r_op = r_mod_op.split(".")
        r_alias, r_target = r_alias_target.split(":")

        mod_match = r_mod in ("*", module)
        op_match = r_op in ("*", "any", operation)
        alias_match = r_alias in ("*", resource)

        # Target match might need more complex parsing for comma-separated lists
        target_match = False
        if r_target == "*":
            target_match = True
        else:
            targets = [t.strip() for t in r_target.split(",")]
            if target in targets:
                target_match = True

        # Security: Verify webhook token (Base64-encoded by the client)
        token_match = False
        if trigger.webhook_token:
            try:
                decoded_token = base64.b64decode(trigger.webhook_token).decode("utf-8")
                token_match = hmac.compare_digest(hook.secret, decoded_token)
            except Exception:
                token_match = False

        if mod_match and op_match and alias_match and target_match and token_match:
            rules_matched.append((name, hook))

    if not rules_matched:
        return

    payload = WebhookPayload(
        event_id=f"evt_{uuid7().hex}",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        source=config.server.host,
        event=WebhookEventDetails(
            module=module,
            operation=operation,
            resource=resource,
            target=target,
            action=action,
            details=details
        ),
        trigger=trigger
    )

    queue = WebhookQueueList.get_queue()
    for name, hook in rules_matched:
        try:
            queue.put_nowait({
                "hook_name": name,
                "url": hook.url,
                "secret": hook.secret,
                "headers": hook.headers,
                "payload": payload.model_dump_json()
            })
        except asyncio.QueueFull:
            logger.error("Webhook queue full, dropping event", event_id=payload.event_id, hook_name=name)
