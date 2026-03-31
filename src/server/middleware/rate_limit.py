import time
import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from config.loader import ConfigManager
from cache.memory import MemoryCache
from cache.redis_backend import RedisCache

logger = structlog.get_logger()

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def get_backend(self):
        config = ConfigManager.get()
        if config.rate_limit.backend == "redis":
            return RedisCache
        elif config.rate_limit.backend == "sqlite":
            from cache.sqlite_backend import SQLiteCache
            return SQLiteCache
        return MemoryCache
        
    async def dispatch(self, request: Request, call_next):
        config = ConfigManager.get()
        if not config.rate_limit.enabled:
            return await call_next(request)

        # 1. Identify client
        client_ip = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
        if isinstance(client_ip, str) and "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()
            
        if client_ip in config.server.allowed_ips:
            return await call_next(request)

        api_key_name = "anonymous"
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            try:
                import base64
                decoded = base64.b64decode(auth_header[7:]).decode("utf-8")
                api_key_name = decoded.split(":")[0]
            except Exception:
                pass
                
        # 2. Determine limits
        limit = config.rate_limit.max_requests
        window = config.rate_limit.window
        
        if api_key_name != "anonymous":
            from security.storage import SecurityStorage
            override = 0
            db_key = SecurityStorage.get_api_key(api_key_name)
            if db_key:
                override = db_key.get("rate_limit_override", 0)
            elif api_key_name in config.api_key:
                override = config.api_key[api_key_name].rate_limit_override
            if override > 0:
                limit = override

        # 3. Apply Atomic Rate Limit Check
        cache = await self.get_backend()
        limit_key = f"rl:ip:{client_ip}:key:{api_key_name}"
        penalty_key = f"penalty:{client_ip}"
        
        violated, current_count = await cache.check_rate_limit(
            limits_key=limit_key,
            window=window,
            limit=limit,
            penalty_key=penalty_key,
            burst=config.rate_limit.burst,
            penalty_cooldown=config.rate_limit.penalty_cooldown
        )
        
        if violated:
            response = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"success": False, "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded or IP temporary blocked."}}
            )
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(int(time.time() + window))
            return response
            
        # Call downstream
        response = await call_next(request)
        
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current_count))
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + window))
        
        return response
