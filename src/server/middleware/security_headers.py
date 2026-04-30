from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ─────────────────────────────────────────────────────────────────────────────
# Pre-computed Header Sets (built once at import time — zero per-request cost)
# ─────────────────────────────────────────────────────────────────────────────

_BASE_HEADERS: tuple = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-xss-protection", b"0"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
    (b"cache-control", b"no-store"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"interest-cohort=()"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
)

_API_HEADERS: tuple = _BASE_HEADERS + (
    (b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"),
    (b"pragma", b"no-cache"),
)

_DOCS_HEADERS: tuple = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-xss-protection", b"0"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"interest-cohort=()"),
)

_DOCS_PREFIXES = ("/api/docs", "/api/spec", "/docs", "/redoc")


class SecurityHeadersMiddleware:
    """ASGIMiddleware to add security headers to every response."""

    __slots__ = ("app",)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _select_headers(path: str) -> tuple:
        """Returns the correct pre-built header set for the given path — O(1) lookup."""
        if any(path.startswith(p) for p in _DOCS_PREFIXES):
            return _DOCS_HEADERS
        if path.startswith("/api/"):
            return _API_HEADERS
        return _BASE_HEADERS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        security_headers = self._select_headers(path)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing_headers = message.setdefault("headers", [])
                existing_keys = {k.lower() for k, _ in existing_headers}
                for key, value in security_headers:
                    if key not in existing_keys:
                        existing_headers.append((key, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)
