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
        return MemoryCache
        
    async def dispatch(self, request: Request, call_next):
        config = ConfigManager.get()
        if not config.rate_limit.enabled:
            return await call_next(request)

        # 1. Identify client
        client_ip = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
        if isinstance(client_ip, str) and "," in client_ip:
            # X-Forwarded-For can contain a list of IPs, the first is the original client
            client_ip = client_ip.split(",")[0].strip()
        if client_ip in config.server.allowed_ips:
            return await call_next(request)

        # Try to parse authorization if present to see if it's a known key
        # (This is a simplified read, real auth happens in the AuthMiddleware)
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
            
            # Check dynamic key
            db_key = SecurityStorage.get_api_key(api_key_name)
            if db_key:
                override = db_key.get("rate_limit_override", 0)
            elif api_key_name in config.api_key:
                override = config.api_key[api_key_name].rate_limit_override
                
            if override > 0:
                limit = override

        # Route specific limits could be injected here based on path
        if request.url.path.endswith("/query"):
            limit = min(limit, 30) # For example

        # 3. Apply Sliding Window
        current_time = time.time()
        window_start = current_time - window
        
        cache = await self.get_backend()
        
        # Simple ip-based key for now. Could do key-based.
        limit_key = f"rl:ip:{client_ip}:key:{api_key_name}"
        
        # We store list of timestamps 
        # (In Redis, normally use sorted sets ZADD, ZREMRANGEBYSCORE. For memory, just a list)
        history = await cache.get(limit_key) or []
        history = [ts for ts in history if ts > window_start]
        
        penalty_key = f"penalty:{client_ip}"
        is_penalized = await cache.get(penalty_key)
        
        if is_penalized:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"success": False, "error": {"code": "RATE_LIMIT_BLOCKED", "message": "IP is temporarily blocked due to excessive requests."}}
            )

        if len(history) >= limit + config.rate_limit.burst:
            # Check consecutive violations
            violation_key = f"rl:violations:{client_ip}"
            violations = (await cache.get(violation_key) or 0) + 1
            await cache.set(violation_key, violations, ttl=window*2)
            
            if violations >= 10:
                await cache.set(penalty_key, True, ttl=config.rate_limit.penalty_cooldown)
                logger.warning("Applied IP penalty", client_ip=client_ip)
                
            response = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"success": False, "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded."}}
            )
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(int(current_time + window))
            return response
            
        # Add to history
        history.append(current_time)
        await cache.set(limit_key, history, ttl=window)
        
        # Call downstream
        response = await call_next(request)
        
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(history)))
        response.headers["X-RateLimit-Reset"] = str(int(current_time + window))
        
        return response
