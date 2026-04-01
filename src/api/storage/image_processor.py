"""
On-the-fly image processing with streaming output.

Processes images using Pillow (resize, format conversion) and streams
the result directly to the client without holding the full output in RAM.
Uses a temporary file as a buffer to avoid memory spikes on large images.
"""
import os
import tempfile
import aiofiles
from starlette.responses import StreamingResponse

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from api.errors import NexusGateException, ErrorCodes

STREAM_CHUNK = 65536  # 64KB


def process_image_and_stream(
    file_path: str,
    width: int = None,
    height: int = None,
    quality: int = 80,
    format: str = None,
) -> StreamingResponse:
    if not HAS_PIL:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, "Image processing requires Pillow library.", 501)

    if not os.path.exists(file_path):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"File not found: {file_path}", 404)

    try:
        img = Image.open(file_path)

        if width and height:
            img.thumbnail((width, height))
        elif width:
            ratio = width / float(img.size[0])
            new_height = int(float(img.size[1]) * ratio)
            img = img.resize((width, new_height), Image.Resampling.LANCZOS)
        elif height:
            ratio = height / float(img.size[1])
            new_width = int(float(img.size[0]) * ratio)
            img = img.resize((new_width, height), Image.Resampling.LANCZOS)

        output_format = format.upper() if format else img.format or "JPEG"
        if output_format == "JPG":
            output_format = "JPEG"

        # Write to a temporary file instead of BytesIO to avoid RAM spikes.
        # The temp file is auto-deleted when the response finishes streaming.
        tmp = tempfile.SpooledTemporaryFile(max_size=1048576)  # Spools in RAM up to 1MB, then spills to disk

        if output_format in ("JPEG", "WEBP"):
            img.save(tmp, format=output_format, quality=quality, optimize=True)
        elif output_format == "PNG":
            img.save(tmp, format=output_format, optimize=True)
        else:
            img.save(tmp, format=output_format)

        # Close the PIL image to free the source file handle immediately
        img.close()

        tmp.seek(0, 2)
        content_length = tmp.tell()
        tmp.seek(0)

        mime_type = f"image/{output_format.lower()}"

        headers = {
            "Content-Length": str(content_length),
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        }

        def sync_streamer():
            """Yield chunks from the spooled temp file."""
            try:
                while True:
                    chunk = tmp.read(STREAM_CHUNK)
                    if not chunk:
                        break
                    yield chunk
            finally:
                tmp.close()

        return StreamingResponse(
            sync_streamer(),
            media_type=mime_type,
            headers=headers,
        )
    except NexusGateException:
        raise
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Image processing failed: {str(e)}", 500)
