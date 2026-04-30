"""
High-performance file streaming with HTTP Range Request support,
ETag-based conditional responses, and CDN-grade caching headers.
"""

import hashlib
import mimetypes
import os
from typing import Optional, Tuple

import aiofiles
from starlette.responses import Response, StreamingResponse

from api.errors import ErrorCodes, NexusGateException
from security.circuit_breaker import CircuitBreaker

CHUNK_SIZE = 65536  # 64KB

# ─────────────────────────────────────────────────────────────────────────────
# Pre-Flight Verifications
# ─────────────────────────────────────────────────────────────────────────────


def get_mime_type(file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"


def _compute_etag(file_stat: os.stat_result) -> str:
    raw = f"{file_stat.st_ino}-{file_stat.st_mtime_ns}-{file_stat.st_size}"
    return f'W/"{hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()}"'


# ─────────────────────────────────────────────────────────────────────────────
# Range Logic Controllers
# ─────────────────────────────────────────────────────────────────────────────


def _evaluate_range_bounds(parts: list, file_size: int) -> Optional[Tuple[int, int]]:
    """Maps index splits isolating standard syntax exceptions securely."""
    if parts[0] == "":
        suffix_len = int(parts[1])
        return (
            (file_size - suffix_len, file_size - 1)
            if 0 < suffix_len <= file_size
            else None
        )

    start = int(parts[0])
    end = int(parts[1]) if parts[1] else file_size - 1
    return (start, end) if 0 <= start < file_size and start <= end < file_size else None


def _parse_range_header(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """Generates byte structures matching 206 execution flows."""
    if not range_header or not range_header.startswith("bytes=") or "," in range_header:
        return None

    try:
        return _evaluate_range_bounds(range_header[6:].strip().split("-", 1), file_size)
    except (ValueError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Native Byte Stream Extractors
# ─────────────────────────────────────────────────────────────────────────────


async def _file_range_streamer(file_path: str, start: int, end: int):
    """Pipes offset partitions exactly bounding chunks."""
    remaining = end - start + 1
    try:
        async with aiofiles.open(file_path, mode="rb") as f:
            await f.seek(start)
            while remaining > 0 and (chunk := await f.read(min(CHUNK_SIZE, remaining))):
                remaining -= len(chunk)
                yield chunk
        CircuitBreaker.record_success("storage_streaming")
    except Exception as e:
        CircuitBreaker.record_failure("storage_streaming")
        raise e


async def _file_streamer(file_path: str):
    """Transmits the entire buffer completely continuously."""
    try:
        async with aiofiles.open(file_path, mode="rb") as f:
            while chunk := await f.read(CHUNK_SIZE):
                yield chunk
        CircuitBreaker.record_success("storage_streaming")
    except Exception as e:
        CircuitBreaker.record_failure("storage_streaming")
        raise e


# ─────────────────────────────────────────────────────────────────────────────
# Web Response Abstraction
# ─────────────────────────────────────────────────────────────────────────────


def _build_base_headers(inline: bool, mime_type: str, filename: str, etag: str) -> dict:
    """Pre-compiles common cache headers separating mutation scopes."""
    headers = {
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"',
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable"
        if mime_type.startswith(("image/", "font/", "audio/", "video/"))
        else "public, max-age=3600",
    }
    return headers


def serve_file(
    path: str, inline: bool = False, request_headers: Optional[dict] = None
) -> Response:
    """Entry node processing HTTP static evaluations mapping streams natively."""
    if CircuitBreaker.is_open("storage_streaming"):
        raise NexusGateException(
            ErrorCodes.SERVER_UNAVAILABLE,
            "Storage streaming circuit is currently OPEN to protect bandwidth.",
            503,
        )

    if not os.path.exists(path) or not os.path.isfile(path):
        raise NexusGateException(
            ErrorCodes.FS_PATH_NOT_FOUND, "File missing on explicit bounds.", 404
        )

    file_stat, req_headers = os.stat(path), request_headers or {}
    etag = _compute_etag(file_stat)

    if req_headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    base_headers = _build_base_headers(
        inline, get_mime_type(path), os.path.basename(path), etag
    )
    byte_range = _parse_range_header(req_headers.get("range", ""), file_stat.st_size)

    if req_headers.get("range", "") and byte_range is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_stat.st_size}", **base_headers},
        )

    if byte_range:
        base_headers.update(
            {
                "Content-Range": f"bytes {byte_range[0]}-{byte_range[1]}/{file_stat.st_size}",
                "Content-Length": str(byte_range[1] - byte_range[0] + 1),
            }
        )
        return StreamingResponse(
            _file_range_streamer(path, byte_range[0], byte_range[1]),
            status_code=206,
            media_type=get_mime_type(path),
            headers=base_headers,
        )

    base_headers["Content-Length"] = str(file_stat.st_size)
    return StreamingResponse(
        _file_streamer(path),
        status_code=200,
        media_type=get_mime_type(path),
        headers=base_headers,
    )
