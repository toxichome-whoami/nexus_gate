"""
Idempotency Middleware: Caches responses by X-Idempotency-Key header.
Duplicate requests with the same key return the cached response without
re-executing the handler. Keys expire after a configurable TTL.
"""
import json
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from cache import CacheManager

logger = structlog.get_logger()

IDEMPOTENCY_HEADER = "X-Idempotency-Key"
IDEMPOTENCY_PREFIX = "idempotency:"
IDEMPOTENCY_TTL = 86400  # 24 hours

# Only cache mutating operations
IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if request.method not in IDEMPOTENT_METHODS:
            return await call_next(request)

        idem_key = request.headers.get(IDEMPOTENCY_HEADER)
        if not idem_key:
            return await call_next(request)

        cache_key = f"{IDEMPOTENCY_PREFIX}{idem_key}"

        # Check for cached response
        cached = await CacheManager.get(cache_key)
        if cached is not None:
            logger.debug("Returning cached idempotent response", key=idem_key)
            response_data = json.loads(cached)
            return JSONResponse(
                content=response_data["body"],
                status_code=response_data["status_code"],
                headers={
                    **response_data.get("headers", {}),
                    "X-Idempotency-Replayed": "true",
                },
            )

        # Execute request
        response = await call_next(request)

        # Cache the response body for idempotent replay
        if response.status_code < 500:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            try:
                await CacheManager.set(
                    cache_key,
                    json.dumps({
                        "status_code": response.status_code,
                        "body": json.loads(body.decode("utf-8")),
                        "headers": dict(response.headers),
                    }),
                    ttl=IDEMPOTENCY_TTL,
                )
            except Exception as e:
                logger.warning("Failed to cache idempotent response", error=str(e))

            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response
