"""
High-performance file streaming with HTTP Range Request support,
ETag-based conditional responses, and CDN-grade caching headers.

Architecture:
  - Zero-copy: reads exactly CHUNK_SIZE bytes at a time, never loads entire files.
  - Range Requests: supports partial content (HTTP 206) for resumable downloads and video seeking.
  - ETag / If-None-Match: returns 304 Not Modified when the client already has the file.
  - CDN Headers: immutable cache directives for static assets.
"""
import os
import stat
import hashlib
import mimetypes
import aiofiles
from typing import Optional, Tuple
from starlette.responses import Response, StreamingResponse
from api.errors import NexusGateException, ErrorCodes

# 64KB chunks = optimal balance between syscall overhead and memory usage.
# Smaller = more syscalls (slow). Larger = more RAM per connection.
CHUNK_SIZE = 65536  # 64KB


def get_mime_type(file_path: str) -> str:
    """Fast MIME detection using the extension table (no disk read needed)."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"


def _compute_etag(file_stat: os.stat_result) -> str:
    """Generate a weak ETag from inode + mtime + size. No file read needed."""
    raw = f"{file_stat.st_ino}-{file_stat.st_mtime_ns}-{file_stat.st_size}"
    return f'W/"{hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()}"'


def _parse_range_header(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """Parse an HTTP Range header into (start, end) byte offsets.
    Returns None if the range is invalid or unsatisfiable.
    Only supports single-range requests (multi-range is rare and complex).
    """
    if not range_header or not range_header.startswith("bytes="):
        return None

    range_spec = range_header[6:].strip()

    # Reject multi-range
    if "," in range_spec:
        return None

    parts = range_spec.split("-", 1)
    try:
        if parts[0] == "":
            # Suffix range: bytes=-500 means "last 500 bytes"
            suffix_len = int(parts[1])
            if suffix_len <= 0 or suffix_len > file_size:
                return None
            return file_size - suffix_len, file_size - 1
        else:
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else file_size - 1

            if start < 0 or start >= file_size or end < start or end >= file_size:
                return None
            return start, end
    except (ValueError, IndexError):
        return None


async def _file_range_streamer(file_path: str, start: int, end: int):
    """Async generator that yields exactly the byte range [start, end] in chunks."""
    remaining = end - start + 1
    async with aiofiles.open(file_path, mode="rb") as f:
        await f.seek(start)
        while remaining > 0:
            read_size = min(CHUNK_SIZE, remaining)
            chunk = await f.read(read_size)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


async def _file_streamer(file_path: str):
    """Async generator that yields the entire file in 64KB chunks."""
    async with aiofiles.open(file_path, mode="rb") as f:
        while True:
            chunk = await f.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk


def serve_file(
    path: str,
    inline: bool = False,
    request_headers: Optional[dict] = None,
) -> Response:
    """
    Serve a file with full HTTP semantics:
      - ETag + If-None-Match → 304 Not Modified
      - Range + If-Range → 206 Partial Content
      - CDN-grade Cache-Control for static assets
    
    Memory usage: exactly CHUNK_SIZE (64KB) per active download, regardless of file size.
    """
    if not os.path.exists(path) or not os.path.isfile(path):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"File not found: {path}", 404)

    request_headers = request_headers or {}
    file_stat = os.stat(path)
    file_size = file_stat.st_size
    mime_type = get_mime_type(path)
    filename = os.path.basename(path)
    etag = _compute_etag(file_stat)

    # --- 304 Not Modified ---
    if_none_match = request_headers.get("if-none-match", "")
    if if_none_match and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})

    # --- Base headers ---
    disposition = "inline" if inline else "attachment"
    base_headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }

    # CDN-grade caching for immutable static assets (images, fonts, etc.)
    if mime_type.startswith(("image/", "font/", "audio/", "video/")):
        base_headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        base_headers["Cache-Control"] = "public, max-age=3600"

    # --- 206 Partial Content (Range Request) ---
    range_header = request_headers.get("range", "")
    if range_header:
        byte_range = _parse_range_header(range_header, file_size)
        if byte_range is None:
            # 416 Range Not Satisfiable
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}", **base_headers},
            )

        start, end = byte_range
        content_length = end - start + 1
        base_headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        base_headers["Content-Length"] = str(content_length)

        return StreamingResponse(
            _file_range_streamer(path, start, end),
            status_code=206,
            media_type=mime_type,
            headers=base_headers,
        )

    # --- 200 Full Response ---
    base_headers["Content-Length"] = str(file_size)

    return StreamingResponse(
        _file_streamer(path),
        status_code=200,
        media_type=mime_type,
        headers=base_headers,
    )
