import time
from typing import Any, Dict, Optional

from fastapi import Request

from config.loader import ConfigManager

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_SERVER_VERSION = "1.0.2"
_SERVER_HOST_CACHE: Optional[str] = None


def _get_server_name() -> str:
    """Cached server name to avoid ConfigManager.get() on every response."""
    global _SERVER_HOST_CACHE
    if _SERVER_HOST_CACHE is None:
        _SERVER_HOST_CACHE = ConfigManager.get().server.host
    return _SERVER_HOST_CACHE


def _build_meta(request: Request, start_time: Optional[float] = None) -> Dict[str, Any]:
    """Build response metadata as a plain dict — no Pydantic overhead."""
    st = (
        start_time
        if start_time is not None
        else getattr(request.state, "start_time", time.perf_counter())
    )
    if st is None:
        st = time.perf_counter()

    duration_ms = (time.perf_counter() - st) * 1000

    return {
        "request_id": getattr(request.state, "request_id", "-"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "duration_ms": round(duration_ms, 2),
        "server": _get_server_name(),
        "version": _SERVER_VERSION,
    }


def success_response(
    request: Request,
    data: Any,
    links: Optional[Dict[str, str]] = None,
    start_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Fast response builder — plain dict, zero Pydantic allocation."""
    resp = {
        "success": True,
        "data": data,
        "meta": _build_meta(request, start_time),
    }
    if links:
        resp["links"] = links
    return resp


def error_response(
    request: Request,
    error_code: str,
    message: str,
    details: Optional[Any] = None,
    start_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Fast error response builder — plain dict, zero Pydantic allocation."""
    error = {"code": error_code, "message": message}
    if details is not None:
        error["details"] = details

    return {
        "success": False,
        "error": error,
        "meta": _build_meta(request, start_time),
    }
