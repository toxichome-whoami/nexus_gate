import os
import shutil
from typing import Dict, Any
from datetime import datetime
import asyncio
from api.errors import NexusGateException, ErrorCodes

async def get_file_info(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
         raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"Path not found: {path}", 404)
         
    stat = os.stat(path)
    is_dir = os.path.isdir(path)
    
    return {
        "name": os.path.basename(path),
        "type": "directory" if is_dir else "file",
        "size": stat.st_size if not is_dir else None,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat()
    }

async def rename_path(source: str, target: str) -> None:
    try:
        os.rename(source, target)
    except FileNotFoundError:
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, "Source path not found", 404)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to rename: {str(e)}", 500)

async def copy_path(source: str, target: str) -> None:
    try:
        # Avoid blocking event loop for large copies
        if os.path.isdir(source):
            await asyncio.to_thread(shutil.copytree, source, target)
        else:
            await asyncio.to_thread(shutil.copy2, source, target)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to copy: {str(e)}", 500)

async def delete_path(source: str) -> None:
    if not os.path.exists(source):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, "Path not found", 404)
    try:
        if os.path.isdir(source):
            await asyncio.to_thread(shutil.rmtree, source)
        else:
            os.remove(source)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to delete: {str(e)}", 500)

async def mkdir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to create directory: {str(e)}", 500)
