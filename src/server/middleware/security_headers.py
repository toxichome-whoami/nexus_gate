from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

class SecurityHeadersMiddleware:
    """ASGIMiddleware to add security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                
                # Path-aware Security Headers
                path = scope.get("path", "")
                is_docs = path.startswith("/api/docs") or path.startswith("/api/spec")
                
                security_headers = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-xss-protection", b"0"),
                    (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"interest-cohort=()"),
                ]

                # Strict CSP by default, relaxed for Swagger UI
                if is_docs:
                    security_headers.append((
                        b"content-security-policy", 
                        b"default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com"
                    ))
                else:
                    security_headers.append((b"content-security-policy", b"default-src 'none'"))
                
                # Append security headers if they don't already exist
                existing_keys = {k.lower() for k, v in headers}
                for key, value in security_headers:
                    if key not in existing_keys:
                        headers.append((key, value))

            await send(message)

        await self.app(scope, receive, send_wrapper)
