import base64
import time

import orjson
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cache.memory import MemoryCache
from cache.redis_backend import RedisCache
from cache.sqlite_backend import SQLiteCache
from config.loader import ConfigManager
from security.storage import SecurityStorage

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_client_ip(scope: Scope, headers: dict) -> str:
    """Extracts the true client IP from standard proxy headers."""
    if b"x-forwarded-for" in headers:
        return headers[b"x-forwarded-for"].decode("latin-1").split(",")[0].strip()

    if b"x-real-ip" in headers:
        return headers[b"x-real-ip"].decode("latin-1")

    client = scope.get("client")
    if client:
        return client[0]

    return "unknown"


def _resolve_api_key_name(headers: dict) -> str:
    """Extracts the key identifier from Bearer headers prior to full authentication."""
    auth_header = headers.get(b"authorization")
    if auth_header and auth_header.startswith(b"Bearer "):
        try:
            decoded = base64.b64decode(auth_header[7:]).decode("utf-8")
            return decoded.split(":")[0]
        except Exception:
            pass
    return "anonymous"


def _determine_effective_limits(api_key_name: str, config) -> int:
    """Calculates exactly how many requests this key is allowed per rolling window."""
    base_limit = config.rate_limit.max_requests

    if api_key_name == "anonymous":
        return base_limit

    # Dynamic cache lookup
    db_key = SecurityStorage.get_api_key(api_key_name)
    if db_key and db_key.get("rate_limit_override", 0) > 0:
        return db_key["rate_limit_override"]

    # Static config fallback
    if api_key_name in config.api_key:
        cfg_override = config.api_key[api_key_name].rate_limit_override
        if cfg_override > 0:
            return cfg_override

    return base_limit


async def _send_rejection_response(send: Send, limit: int, window: int):
    """Fires a standard HTTP 429 JSON response payload with proper headers."""
    body = orjson.dumps(
        {
            "success": False,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Rate limit exceeded or IP temporary blocked.",
            },
        }
    )

    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-ratelimit-limit", str(limit).encode("ascii")),
                (b"x-ratelimit-remaining", b"0"),
                (b"x-ratelimit-reset", str(int(time.time() + window)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ─────────────────────────────────────────────────────────────────────────────
# Class Implementation
# ─────────────────────────────────────────────────────────────────────────────


class RateLimitMiddleware:
    """Stateful ASGI middleware bridging cached sliding-window limits."""

    __slots__ = (
        "app",
        "_backend",
        "_enabled",
        "_allowed_ips",
        "_window",
        "_burst",
        "_penalty_cooldown",
    )

    def __init__(self, app: ASGIApp):
        self.app = app
        # Resolve config and backend class ONCE at startup - never per-request
        config = ConfigManager.get()
        self._enabled = config.rate_limit.enabled
        self._allowed_ips = config.server.allowed_ips
        self._window = config.rate_limit.window
        self._burst = config.rate_limit.burst
        self._penalty_cooldown = config.rate_limit.penalty_cooldown
        if config.rate_limit.backend == "redis":
            self._backend = RedisCache
        elif config.rate_limit.backend == "sqlite":
            self._backend = SQLiteCache
        else:
            self._backend = MemoryCache

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Core ASGI entrypoint processing the event loop injection."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if not self._enabled:
            return await self.app(scope, receive, send)

        config = ConfigManager.get()

        headers = dict(scope.get("headers", []))
        client_ip = _resolve_client_ip(scope, headers)

        if client_ip in self._allowed_ips:
            return await self.app(scope, receive, send)

        api_key_name = _resolve_api_key_name(headers)

        limit = _determine_effective_limits(api_key_name, config)

        # Use pre-cached backend class - no per-request resolution
        violated, current_count = await self._backend.check_rate_limit(
            limits_key=f"rl:ip:{client_ip}:key:{api_key_name}",
            window=self._window,
            limit=limit,
            penalty_key=f"penalty:{client_ip}",
            burst=self._burst,
            penalty_cooldown=self._penalty_cooldown,
        )

        if violated:
            return await _send_rejection_response(send, limit, self._window)

        async def send_wrapper(message: Message) -> None:
            """Injected hook wrapping the final request phase to enforce header attachments."""
            if message["type"] == "http.response.start":
                resp_headers = message.setdefault("headers", [])
                resp_headers.append((b"x-ratelimit-limit", str(limit).encode("ascii")))
                resp_headers.append(
                    (
                        b"x-ratelimit-remaining",
                        str(max(0, limit - current_count)).encode("ascii"),
                    )
                )
                resp_headers.append(
                    (
                        b"x-ratelimit-reset",
                        str(int(time.time() + self._window)).encode("ascii"),
                    )
                )
            await send(message)

        # Allow execution downward
        await self.app(scope, receive, send_wrapper)
