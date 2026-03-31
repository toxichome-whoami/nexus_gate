import time
import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = structlog.get_logger()

class LoggingMiddleware:
    __slots__ = ("app",)

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start_time = time.perf_counter()
        
        # We need to wrap 'send' to intercept the response status code
        status_code = [500] 
        
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code[0] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            client = scope.get("client", ("unknown", 0))
            client_ip = client[0] if client else "unknown"
            
            headers = dict(scope.get("headers", []))
            x_forwarded_for = headers.get(b"x-forwarded-for", b"").decode("latin-1")
            if x_forwarded_for:
                client_ip = x_forwarded_for.split(",")[0].strip()
                
            logger.error(
                "Request failed",
                request_id=scope.get("state", {}).get("request_id", "-"),
                method=scope.get("method"),
                path=scope.get("path"),
                client_ip=client_ip,
                duration_ms=round(duration_ms, 2),
                error=str(e),
                exc_info=True
            )
            raise
            
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        client = scope.get("client", ("unknown", 0))
        client_ip = client[0] if client else "unknown"
        headers = dict(scope.get("headers", []))
        x_forwarded_for = headers.get(b"x-forwarded-for", b"").decode("latin-1")
        if x_forwarded_for:
            client_ip = x_forwarded_for.split(",")[0].strip()

        sc = status_code[0]
        level = logger.info
        if sc >= 500:
            level = logger.error
        elif sc >= 400:
            level = logger.warning

        level(
            "Request completed",
            request_id=scope.get("state", {}).get("request_id", "-"),
            method=scope.get("method"),
            path=scope.get("path"),
            client_ip=client_ip,
            status=sc,
            duration_ms=round(duration_ms, 2)
        )
