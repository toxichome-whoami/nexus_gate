import asyncio
import base64
import hmac
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import structlog
from pydantic import BaseModel

from config.provider import GlobalConfigProvider
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
    _maxsize: int = 0

    @classmethod
    def get_queue(cls) -> asyncio.Queue:
        if cls._queue is None:
            config = GlobalConfigProvider().get_config()
            cls._maxsize = config.webhooks.queue_size
            cls._queue = asyncio.Queue(maxsize=cls._maxsize)
        return cls._queue


# ─────────────────────────────────────────────────────────────────────────────
# Pre-Compiled Rule Cache
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompiledRule:
    """Pre-parsed rule components for O(1) matching per event."""

    module: str
    operation: str
    alias: str
    targets: Tuple[str, ...]  # Immutable tuple for fast 'in' checks


# {hook_name: CompiledRule}
_compiled_rules: Dict[str, CompiledRule] = {}


def _compile_rules() -> None:
    """Pre-compiles all webhook rules from config into structured objects.
    Called once at startup and again on config hot-reload."""
    global _compiled_rules
    config = GlobalConfigProvider().get_config()
    if not config.features.webhook or not config.webhooks.enabled:
        _compiled_rules = {}
        return

    new_rules: Dict[str, CompiledRule] = {}
    for name, hook in config.webhook.items():
        try:
            r_mod_op, r_alias_target = hook.rule.split("@")
            r_mod, r_op = r_mod_op.split(".")
            r_alias, r_target = r_alias_target.split(":")
            targets = (
                tuple(t.strip() for t in r_target.split(","))
                if r_target != "*"
                else ("*",)
            )
            new_rules[name] = CompiledRule(
                module=r_mod,
                operation=r_op,
                alias=r_alias,
                targets=targets,
            )
        except (ValueError, AttributeError) as e:
            logger.warning("Skipping malformed webhook rule", hook=name, error=str(e))
            continue

    _compiled_rules = new_rules


# Compile on first import
_compile_rules()


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


def _is_rule_matched_compiled(
    compiled: CompiledRule,
    hook,
    module: str,
    operation: str,
    resource: str,
    target: str,
    trigger: WebhookTrigger,
) -> bool:
    """O(1) rule matching against pre-compiled rule components."""
    if not hook.enabled:
        return False

    if compiled.module not in ("*", module):
        return False

    if compiled.operation not in ("*", "any", operation):
        return False

    if compiled.alias not in ("*", resource):
        return False

    if compiled.targets[0] != "*" and target not in compiled.targets:
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


async def emit_event(
    module: str,
    operation: str,
    resource: str,
    target: str,
    action: str,
    details: Dict[str, Any],
    trigger: WebhookTrigger,
) -> None:
    """Async entry point for webhook emission with pre-compiled O(1) rule matching."""
    config = GlobalConfigProvider().get_config()
    if not config.features.webhook or not config.webhooks.enabled:
        return

    # Match against pre-compiled rules — no string parsing per event
    matched_hooks = []
    for name, hook in config.webhook.items():
        compiled = _compiled_rules.get(name)
        if compiled is None:
            continue
        if _is_rule_matched_compiled(
            compiled, hook, module, operation, resource, target, trigger
        ):
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
