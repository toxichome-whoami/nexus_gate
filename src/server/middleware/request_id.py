from starlette.types import ASGIApp, Scope, Receive, Send, Message
from utils.uuid7 import uuid7

class RequestIDMiddleware:
    """Middleware to ensure every request has an X-Request-ID (UUID v7)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    def _resolve_request_id(self, headers: dict) -> str:
        """Extracts existing tracing ID or generates a new chronological UUIDv7."""
        req_id_bytes = headers.get(b"x-request-id", b"")
        if req_id_bytes:
            return req_id_bytes.decode("ascii")
        return f"req_{uuid7().hex}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        req_id = self._resolve_request_id(headers)

        # Attach securely for downstream processors (logging)
        scope.setdefault("state", {})["request_id"] = req_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = message.setdefault("headers", [])
                existing_keys = {k for k, v in resp_headers}
                
                if b"x-request-id" not in existing_keys:
                    resp_headers.append((b"x-request-id", req_id.encode("ascii")))

            await send(message)

        await self.app(scope, receive, send_wrapper)
