import os
from fastapi.responses import StreamingResponse
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from api.errors import NexusGateException, ErrorCodes

import io

def process_image_and_stream(file_path: str, width: int = None, height: int = None, quality: int = 80, format: str = None) -> StreamingResponse:
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
            height = int((float(img.size[1]) * float(ratio)))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        elif height:
            ratio = height / float(img.size[1])
            width = int((float(img.size[0]) * float(ratio)))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
            
        output_format = format.upper() if format else img.format or "JPEG"
        if output_format == "JPG":
            output_format = "JPEG"
            
        img_byte_arr = io.BytesIO()
        
        # Only apply quality to JPEG/WEBP
        if output_format in ["JPEG", "WEBP"]:
            img.save(img_byte_arr, format=output_format, quality=quality)
        else:
            img.save(img_byte_arr, format=output_format)
            
        img_byte_arr.seek(0)
        
        mime_type = f"image/{output_format.lower()}"
        
        headers = {
            "Content-Length": str(img_byte_arr.getbuffer().nbytes),
            "Cache-Control": "public, max-age=31536000, immutable"
        }
        
        # Streaming from a BytesIO requires an async generator
        async def bytes_streamer():
            yield img_byte_arr.read()
            
        return StreamingResponse(
            bytes_streamer(),
            media_type=mime_type,
            headers=headers
        )
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Image processing failed: {str(e)}", 500)
