from starlette.types import ASGIApp, Message, Receive, Scope, Send

class SecurityHeadersMiddleware:
    """ASGIMiddleware to add security headers to every response."""
    __slots__ = ("app",)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    def _generate_security_headers(self, path: str) -> list[tuple[bytes, bytes]]:
        """Constructs response headers specific to the requested URI."""
        # OWASP Security Headers Base
        headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"x-xss-protection", b"0"),
            (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
            (b"cache-control", b"no-store"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"interest-cohort=()"),
        ]

        # Documentation endpoints require relaxed CSP
        is_docs = any(path.startswith(prefix) for prefix in ("/api/docs", "/api/spec", "/docs", "/redoc"))
        if not is_docs:
            headers.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))

        # Disable cache purely for /api
        if path.startswith("/api/"):
            headers.append((b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"))
            headers.append((b"pragma", b"no-cache"))
            
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing_headers = message.setdefault("headers", [])
                
                path = scope.get("path", "")
                security_headers = self._generate_security_headers(path)
                
                existing_keys = {k.lower() for k, v in existing_headers}
                for key, value in security_headers:
                    if key not in existing_keys:
                        existing_headers.append((key, value))

            await send(message)

        await self.app(scope, receive, send_wrapper)
