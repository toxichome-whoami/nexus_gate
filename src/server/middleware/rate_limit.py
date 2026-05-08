import base64
import functools
import time

import orjson
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cache.memory import MemoryCache
from cache.redis_backend import RedisCache
from cache.sqlite_backend import SQLiteCache
from config.provider import GlobalConfigProvider
from security.storage import SecurityStorage

logger = structlog.get_logger()

_HTTP = "http"
_HTTP_RESPONSE_START = "http.response.start"
_XFF_HEADER = b"x-forwarded-for"
_XRI_HEADER = b"x-real-ip"
_AUTH_HEADER = b"authorization"
_BEARER_PREFIX = b"Bearer "

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_client_ip_from_headers(headers: tuple) -> str:
    """Extracts the true client IP scanning raw header tuples directly."""
    client_ip = None
    for k, v in headers:
        if k == _XFF_HEADER:
            return v.decode("latin-1").split(",", 1)[0].strip()
        elif k == _XRI_HEADER:
            client_ip = v.decode("latin-1")
    return client_ip  # type: ignore


def _resolve_api_key_from_headers(headers: tuple) -> str:
    """Extracts the key identifier from Bearer headers scanning raw tuples."""
    for k, v in headers:
        if k == _AUTH_HEADER and v.startswith(_BEARER_PREFIX):
            try:
                decoded = base64.b64decode(v[7:]).decode("utf-8")
                return decoded.split(":", 1)[0]
            except Exception:
                return "anonymous"
    return "anonymous"


_determine_effective_limits_cache = None


def _get_determine_effective_limits_cache():
    global _determine_effective_limits_cache
    if _determine_effective_limits_cache is None:
        config = GlobalConfigProvider().get_config()
        maxsize = 256
        if hasattr(config, "performance") and hasattr(
            config.performance, "rate_limit_cache_size"
        ):
            maxsize = config.performance.rate_limit_cache_size
        _determine_effective_limits_cache = functools.lru_cache(maxsize=maxsize)(
            _determine_effective_limits_impl
        )
    return _determine_effective_limits_cache


def _determine_effective_limits_impl(api_key_name: str) -> int:
    """Calculates exactly how many requests this key is allowed per rolling window.
    Cached to avoid redundant SecurityStorage lookups on every request."""
    config = GlobalConfigProvider().get_config()
    base_limit = config.rate_limit.max_requests

    if api_key_name == "anonymous":
        return base_limit

    # Static config fallback (fast path)
    if api_key_name in config.api_key:
        cfg_override = config.api_key[api_key_name].rate_limit_override
        if cfg_override > 0:
            return cfg_override

    # Dynamic cache lookup
    db_key = SecurityStorage.get_api_key(api_key_name)
    if db_key and db_key.get("rate_limit_override", 0) > 0:
        return db_key["rate_limit_override"]

    return base_limit


def _determine_effective_limits(api_key_name: str) -> int:
    return _get_determine_effective_limits_cache()(api_key_name)


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

    retry_after = str(window).encode("ascii")
    reset_time = str(int(time.time() + window)).encode("ascii")
    limit_bytes = str(limit).encode("ascii")

    await send(
        {
            "type": _HTTP_RESPONSE_START,
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"retry-after", retry_after),
                (b"x-ratelimit-limit", limit_bytes),
                (b"x-ratelimit-remaining", b"0"),
                (b"x-ratelimit-reset", reset_time),
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
        "_penalty_threshold",
    )

    def __init__(self, app: ASGIApp):
        self.app = app
        # Resolve config and backend class ONCE at startup - never per-request
        config = GlobalConfigProvider().get_config()
        self._enabled = config.rate_limit.enabled
        self._allowed_ips = frozenset(config.server.allowed_ips)  # frozenset for O(1)
        self._window = config.rate_limit.window
        self._burst = config.rate_limit.burst
        self._penalty_cooldown = config.rate_limit.penalty_cooldown
        self._penalty_threshold = config.rate_limit.penalty_threshold
        if config.rate_limit.backend == "redis":
            self._backend = RedisCache
        elif config.rate_limit.backend == "sqlite":
            self._backend = SQLiteCache
        else:
            self._backend = MemoryCache

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Core ASGI entrypoint processing the event loop injection."""
        if scope["type"] != _HTTP:
            return await self.app(scope, receive, send)

        if not self._enabled:
            return await self.app(scope, receive, send)

        # Scan raw headers directly — no dict() allocation
        raw_headers = scope.get("headers", ())

        # Try to extract client IP from scope first (cheapest)
        client_ip = None
        for k, v in raw_headers:
            if k == _XFF_HEADER:
                client_ip = v.decode("latin-1").split(",", 1)[0].strip()
                break
            elif k == _XRI_HEADER:
                client_ip = v.decode("latin-1")

        if client_ip is None:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"

        if client_ip in self._allowed_ips:
            return await self.app(scope, receive, send)

        api_key_name = _resolve_api_key_from_headers(raw_headers)
        limit = _determine_effective_limits(api_key_name)

        # Use pre-cached backend class - no per-request resolution
        violated, current_count = await self._backend.check_rate_limit(
            limits_key=f"rl:ip:{client_ip}:key:{api_key_name}",
            window=self._window,
            limit=limit,
            penalty_key=f"penalty:{client_ip}",
            burst=self._burst,
            penalty_cooldown=self._penalty_cooldown,
            penalty_threshold=self._penalty_threshold,
        )

        if violated:
            return await _send_rejection_response(send, limit, self._window)

        # Pre-compute header values outside the closure
        limit_bytes = str(limit).encode("ascii")
        remaining_bytes = str(max(0, limit - current_count)).encode("ascii")
        reset_bytes = str(int(time.time() + self._window)).encode("ascii")

        async def send_wrapper(message: Message) -> None:
            """Injected hook wrapping the final request phase to enforce header attachments."""
            if message["type"] == _HTTP_RESPONSE_START:
                resp_headers = message.setdefault("headers", [])
                resp_headers.append((b"x-ratelimit-limit", limit_bytes))
                resp_headers.append((b"x-ratelimit-remaining", remaining_bytes))
                resp_headers.append((b"x-ratelimit-reset", reset_bytes))
            await send(message)

        # Allow execution downward
        await self.app(scope, receive, send_wrapper)
