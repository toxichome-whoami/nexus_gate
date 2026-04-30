"""
On-the-fly image processing with streaming output.
Processes images using Pillow (resize, format conversion) and streams
the result directly to the client without holding the full output in RAM.
"""

import os
import tempfile
from typing import Any, Optional

from starlette.responses import StreamingResponse

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image: Any = None

from api.errors import ErrorCodes, NexusGateException

STREAM_CHUNK = 65536  # 64KB

# ─────────────────────────────────────────────────────────────────────────────
# Resizing Handlers
# ─────────────────────────────────────────────────────────────────────────────


def _apply_resizing_filters(
    img, width: Optional[int] = None, height: Optional[int] = None
):
    """Executes spatial adjustments ensuring memory efficiency dynamically."""
    if width and height:
        img.thumbnail((width, height))
    elif width:
        ratio = width / float(img.size[0])
        img = img.resize(
            (width, int(float(img.size[1]) * ratio)), Image.Resampling.LANCZOS
        )
    elif height:
        ratio = height / float(img.size[1])
        img = img.resize(
            (int(float(img.size[0]) * ratio), height), Image.Resampling.LANCZOS
        )
    return img


def _resolve_image_format(img, req_format: Optional[str]) -> str:
    """Standardizes target encodings to valid PIL mappings."""
    output = req_format.upper() if req_format else img.format or "JPEG"
    return "JPEG" if output == "JPG" else output


# ─────────────────────────────────────────────────────────────────────────────
# IO Streaming Generators
# ─────────────────────────────────────────────────────────────────────────────


def _stream_temp_buffer(tmp: tempfile.SpooledTemporaryFile):
    """Yields chunks from the spooled temp file isolating file closures natively."""
    try:
        while chunk := tmp.read(STREAM_CHUNK):
            yield chunk
    finally:
        tmp.close()


def _package_streaming_response(
    tmp: tempfile.SpooledTemporaryFile, output_format: str
) -> StreamingResponse:
    """Bundles headers executing standard web streaming protocols."""
    tmp.seek(0, 2)
    content_length = tmp.tell()
    tmp.seek(0)

    return StreamingResponse(
        _stream_temp_buffer(tmp),
        media_type=f"image/{output_format.lower()}",
        headers={
            "Content-Length": str(content_length),
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Primary Routine
# ─────────────────────────────────────────────────────────────────────────────


def process_image_and_stream(
    file_path: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    quality: int = 80,
    format: Optional[str] = None,
) -> StreamingResponse:
    if not HAS_PIL:
        raise NexusGateException(
            ErrorCodes.SERVER_INTERNAL, "Image processing requires Pillow library.", 501
        )
    if not os.path.exists(file_path):
        raise NexusGateException(
            ErrorCodes.FS_PATH_NOT_FOUND, f"File not found: {file_path}", 404
        )

    try:
        img = _apply_resizing_filters(Image.open(file_path), width, height)
        output_format = _resolve_image_format(img, format)

        tmp = tempfile.SpooledTemporaryFile(max_size=1048576)

        if output_format in ("JPEG", "WEBP", "PNG"):
            img.save(
                tmp,
                format=output_format,
                quality=quality if output_format != "PNG" else None,
                optimize=True,
            )
        else:
            img.save(tmp, format=output_format)

        img.close()
        return _package_streaming_response(tmp, output_format)

    except NexusGateException:
        raise
    except Exception as e:
        raise NexusGateException(
            ErrorCodes.SERVER_INTERNAL, f"Image processing failed: {str(e)}", 500
        )
