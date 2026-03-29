import time
import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = structlog.get_logger()

class LoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()

        # Bind request_id if available from request.state
        req_id = getattr(request.state, "request_id", "-")
        
        # Log request start (optional, usually end is enough)
        
        try:
            response = await call_next(request)
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # Use request.client.host instead of trusting X-Forwarded-For implicitly here,
            # though WAF or an upstream proxy setup will define real ip.
            client_ip = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
            if isinstance(client_ip, str) and "," in client_ip:
                client_ip = client_ip.split(",")[0].strip()
            
            logger.error(
                "Request failed",
                request_id=req_id,
                method=request.method,
                path=request.url.path,
                client_ip=client_ip,
                duration_ms=round(duration_ms, 2),
                error=str(e),
                exc_info=True
            )
            raise
            
        duration_ms = (time.perf_counter() - start_time) * 1000
        client_ip = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
        if isinstance(client_ip, str) and "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()
        
        # Determine log level based on status
        level = logger.info
        if response.status_code >= 500:
            level = logger.error
        elif response.status_code >= 400:
            level = logger.warning

        level(
            "Request completed",
            request_id=req_id,
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
            status=response.status_code,
            duration_ms=round(duration_ms, 2)
        )
        
        return response
