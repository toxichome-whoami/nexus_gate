"""
Idempotency Middleware: Caches responses by X-Idempotency-Key header.
Duplicate requests with the same key return the cached response without
re-executing the handler. Keys expire after a configurable TTL.
"""

from typing import Any, Optional

import orjson
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cache import CacheManager

logger = structlog.get_logger()

IDEMPOTENCY_PREFIX = "idempotency:"
IDEMPOTENCY_TTL = 86400  # 24 hours
IDEMPOTENT_METHODS = {b"POST", b"PUT", b"PATCH", b"DELETE"}


class IdempotencyMiddleware:
    """Safeguards mutation endpoints against duplicate retry requests."""

    __slots__ = ("app",)

    def __init__(self, app: ASGIApp):
        self.app = app

    # ─────────────────────────────────────────────────────────────────────────────
    # Internal Handlers
    # ─────────────────────────────────────────────────────────────────────────────

    async def _extract_idempotency_key(self, scope: Scope) -> Optional[str]:
        """Validates if the request warrants state tracking."""
        method = scope.get("method", "").encode("ascii")
        if method not in IDEMPOTENT_METHODS:
            return None

        headers = dict(scope.get("headers", []))
        idem_key = headers.get(b"x-idempotency-key")

        if not idem_key:
            return None

        return idem_key.decode("latin-1")

    async def _serve_cached_response(
        self, send: Send, idem_key_str: str, cached: Any
    ) -> bool:
        """Parses and transmits a previously cached response."""
        logger.debug("Returning cached idempotent response", key=idem_key_str)
        try:
            if isinstance(cached, str):
                cached = orjson.loads(cached)

            status_code = cached[0]
            resp_headers = [
                (k.encode("latin-1"), v.encode("latin-1")) for k, v in cached[1]
            ]
            resp_headers.append((b"x-idempotency-replayed", b"true"))
            body_bytes = bytes.fromhex(cached[2])

            await send(
                {
                    "type": "http.response.start",
                    "status": status_code,
                    "headers": resp_headers,
                }
            )
            await send({"type": "http.response.body", "body": body_bytes})
            return True
        except Exception as e:
            logger.warning("Failed to parse cached idempotent response", error=str(e))
            return False

    async def _cache_response(
        self, cache_key: str, res_status: int, res_headers: list, res_body: bytearray
    ):
        """Constructs and pushes a compact layout to the cache backend."""
        try:
            # Format: [status, [[k, v]], hex_body]
            serializable_headers = [
                [k.decode("latin-1"), v.decode("latin-1")]
                for k, v in res_headers
                if k != b"x-idempotency-replayed"
            ]
            payload = [res_status, serializable_headers, res_body.hex()]

            await CacheManager.set(
                cache_key,
                orjson.dumps(payload),
                ttl=IDEMPOTENCY_TTL,
            )
        except Exception as e:
            logger.warning("Failed to cache idempotent response", error=str(e))

    # ─────────────────────────────────────────────────────────────────────────────
    # Core Injection
    # ─────────────────────────────────────────────────────────────────────────────

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        idem_key_str = await self._extract_idempotency_key(scope)
        if not idem_key_str:
            return await self.app(scope, receive, send)

        cache_key = f"{IDEMPOTENCY_PREFIX}{idem_key_str}"

        # 1. Check for Cached Hit
        cached = await CacheManager.get(cache_key)
        if cached is not None:
            served = await self._serve_cached_response(send, idem_key_str, cached)
            if served:
                return

        # 2. Intercept Response Live
        response_started = False
        res_status = 200
        res_headers = []
        res_body = bytearray()

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started, res_status, res_headers

            if message["type"] == "http.response.start":
                res_status = message["status"]
                res_headers = message.get("headers", [])
                response_started = True

            elif message["type"] == "http.response.body":
                if res_status < 500:
                    res_body.extend(message.get("body", b""))

            await send(message)

        await self.app(scope, receive, send_wrapper)

        # 3. Cache Result Asynchronously
        if response_started and res_status < 500 and len(res_body) > 0:
            await self._cache_response(cache_key, res_status, res_headers, res_body)
