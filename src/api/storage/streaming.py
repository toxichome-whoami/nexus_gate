import os
import mimetypes
from fastapi.responses import StreamingResponse
from api.errors import NexusGateException, ErrorCodes

import aiofiles

def get_mime_type(file_path: str) -> str:
    # We could use python-magic here, but mimetypes is often sufficient
    # and faster for basic web serving.
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or 'application/octet-stream'

async def file_streamer(file_path: str):
    async with aiofiles.open(file_path, mode="rb") as f:
        while chunk := await f.read(65536):
            yield chunk

def serve_file(path: str, inline: bool = False) -> StreamingResponse:
    if not os.path.exists(path) or not os.path.isfile(path):
         raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"File not found: {path}", 404)
         
    mime_type = get_mime_type(path)
    filename = os.path.basename(path)
    
    headers = {
        "Content-Length": str(os.path.getsize(path))
    }
    
    if mime_type.startswith("image/"):
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
        
    if not inline:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    else:
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
        
    return StreamingResponse(
        file_streamer(path), 
        media_type=mime_type, 
        headers=headers
    )
