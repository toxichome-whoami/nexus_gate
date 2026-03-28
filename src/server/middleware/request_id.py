from starlette.types import ASGIApp, Scope, Receive, Send, Message
import time
import os
from utils.uuid7 import uuid7

class RequestIDMiddleware:
    """Middleware to ensure every request has an X-Request-ID (UUID v7)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        
        # Try to find existing X-Request-ID
        req_id = headers.get(b"x-request-id", b"").decode("ascii")
        if not req_id:
            # Generate a new UUIDv7 string. Prefix as req_xxx
            req_id = f"req_{uuid7().hex}"
            
            # Note: We don't mutate scope['headers'] directly as it could break some 
            # downstream tools expecting unmodified headers, but we could if needed.
            # Instead we attach it to state if using starlette requests. Since we are pure ASGI here:
            scope.setdefault("state", {})["request_id"] = req_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = message.setdefault("headers", [])
                
                # Ensure it's in the response
                existing_keys = {k for k, v in resp_headers}
                if b"x-request-id" not in existing_keys:
                    resp_headers.append((b"x-request-id", req_id.encode("ascii")))
                    
            await send(message)

        await self.app(scope, receive, send_wrapper)
