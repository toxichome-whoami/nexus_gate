import time
from typing import Any, Dict, Optional

from fastapi import Request

from config.loader import ConfigManager
from utils.types import ErrorDetails, RequestMeta, ResponseEnvelope


def _get_meta(request: Request, start_time: Optional[float] = None) -> RequestMeta:
    st = (
        start_time
        if start_time is not None
        else getattr(request.state, "start_time", time.perf_counter())
    )
    if st is None:
        st = time.perf_counter()

    duration_ms = (time.perf_counter() - st) * 1000

    server_name = ConfigManager.get().server.host

    return RequestMeta(
        request_id=getattr(request.state, "request_id", "-"),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        duration_ms=round(duration_ms, 2),
        server=server_name,
        version="1.0.2",
    )


def success_response(
    request: Request,
    data: Any,
    links: Optional[Dict[str, str]] = None,
    start_time: Optional[float] = None,
) -> dict:
    meta = _get_meta(request, start_time)

    return ResponseEnvelope(success=True, data=data, meta=meta, links=links).model_dump(
        exclude_none=True
    )


def error_response(
    request: Request,
    error_code: str,
    message: str,
    details: Optional[Any] = None,
    start_time: Optional[float] = None,
) -> dict:
    meta = _get_meta(request, start_time)

    return ResponseEnvelope(
        success=False,
        error=ErrorDetails(code=error_code, message=message, details=details),
        meta=meta,
    ).model_dump(exclude_none=True)
