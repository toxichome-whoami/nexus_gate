import time
import base64
import structlog
import orjson
from starlette.types import ASGIApp, Scope, Receive, Send, Message

from config.loader import ConfigManager
from cache.memory import MemoryCache
from cache.redis_backend import RedisCache
from cache.sqlite_backend import SQLiteCache
from security.storage import SecurityStorage

logger = structlog.get_logger()

class RateLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def get_backend(self):
        config = ConfigManager.get()
        if config.rate_limit.backend == "redis":
            return RedisCache
        elif config.rate_limit.backend == "sqlite":
            return SQLiteCache
        return MemoryCache
        
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        config = ConfigManager.get()
        if not config.rate_limit.enabled:
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))

        # 1. Identify client
        client_ip = "unknown"
        if b"x-forwarded-for" in headers:
            client_ip = headers[b"x-forwarded-for"].decode("latin-1").split(",")[0].strip()
        elif b"x-real-ip" in headers:
            client_ip = headers[b"x-real-ip"].decode("latin-1")
        else:
            client = scope.get("client")
            if client:
                client_ip = client[0]

        if client_ip in config.server.allowed_ips:
            return await self.app(scope, receive, send)

        api_key_name = "anonymous"
        auth_header = headers.get(b"authorization")
        if auth_header and auth_header.startswith(b"Bearer "):
            try:
                decoded = base64.b64decode(auth_header[7:]).decode("utf-8")
                api_key_name = decoded.split(":")[0]
            except Exception:
                pass
                
        # 2. Determine limits
        limit = config.rate_limit.max_requests
        window = config.rate_limit.window
        
        if api_key_name != "anonymous":
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
            body = orjson.dumps({
                "success": False, 
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED", 
                    "message": "Rate limit exceeded or IP temporary blocked."
                }
            })
            
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"x-ratelimit-limit", str(limit).encode("ascii")),
                    (b"x-ratelimit-remaining", b"0"),
                    (b"x-ratelimit-reset", str(int(time.time() + window)).encode("ascii"))
                ]
            })
            await send({"type": "http.response.body", "body": body})
            return
            
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = message.setdefault("headers", [])
                resp_headers.append((b"x-ratelimit-limit", str(limit).encode("ascii")))
                resp_headers.append((b"x-ratelimit-remaining", str(max(0, limit - current_count)).encode("ascii")))
                resp_headers.append((b"x-ratelimit-reset", str(int(time.time() + window)).encode("ascii")))
            await send(message)

        await self.app(scope, receive, send_wrapper)
