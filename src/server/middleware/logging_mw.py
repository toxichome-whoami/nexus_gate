import time
from typing import Optional

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()


class LoggingMiddleware:
    """Logs lifecycle and latency execution details for all requests."""

    __slots__ = ("app",)

    def __init__(self, app: ASGIApp):
        self.app = app

    # ─────────────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────────────────

    def _get_client_ip(self, scope: Scope) -> str:
        """Resolves the true client IP safely from ASGI scope."""
        headers = dict(scope.get("headers", []))
        x_forwarded_for = headers.get(b"x-forwarded-for", b"").decode("latin-1")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()

        client = scope.get("client")
        return client[0] if client else "unknown"

    def _log_completion(
        self,
        scope: Scope,
        duration_ms: float,
        status_code: int,
        ip: str,
        error: Optional[Exception] = None,
    ):
        """Dispatches structured log payloads respecting log levels."""
        req_id = scope.get("state", {}).get("request_id", "-")
        method = scope.get("method")
        path = scope.get("path")

        if error:
            logger.error(
                "Request failed",
                request_id=req_id,
                method=method,
                path=path,
                client_ip=ip,
                duration_ms=round(duration_ms, 2),
                error=str(error),
                exc_info=True,
            )
            return

        level = logger.info
        if status_code >= 500:
            level = logger.error
        elif status_code >= 400:
            level = logger.warning

        level(
            "Request completed",
            request_id=req_id,
            method=method,
            path=path,
            client_ip=ip,
            status=status_code,
            duration_ms=round(duration_ms, 2),
        )

    # ─────────────────────────────────────────────────────────────────────────────
    # Core Injection
    # ─────────────────────────────────────────────────────────────────────────────

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start_time = time.perf_counter()
        status_code = [500]
        client_ip = self._get_client_ip(scope)

        async def send_wrapper(message: Message):
            if message["type"] == "http.response.start":
                status_code[0] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self._log_completion(scope, duration_ms, status_code[0], client_ip, error=e)
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        self._log_completion(scope, duration_ms, status_code[0], client_ip)
