import os
from fastapi.responses import StreamingResponse
import zipstream
from api.errors import NexusGateException, ErrorCodes

def stream_zip_folder(folder_path: str, filename: str) -> StreamingResponse:
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"Folder not found: {folder_path}", 404)
        
    z = zipstream.ZipFile(mode='w', compression=zipstream.ZIP_DEFLATED)
    
    # Walk through folder and add files
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            full_path = os.path.join(root, file)
            # Create relative path for inside the zip
            rel_path = os.path.relpath(full_path, folder_path)
            z.write(full_path, arcname=rel_path)
            
    # Set headers
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}.zip"',
    }
    
    # zipstream yields chunks of the zip file
    def zip_streamer():
        yield from z
        
    return StreamingResponse(
        zip_streamer(),
        media_type="application/zip",
        headers=headers
    )
