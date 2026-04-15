import os
import shutil
from typing import Dict, Any, List
from datetime import datetime
import asyncio
import mimetypes
from utils.size_parser import format_size
from api.errors import NexusGateException, ErrorCodes

# ─────────────────────────────────────────────────────────────────────────────
# Extraction Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_directory_count(path: str) -> int:
    """Safely intercepts local permission or IO errors reading nested bounds."""
    try:
        return len(os.listdir(path))
    except Exception:
        return 0

def _enrich_file_metrics(stat, path: str, res: dict) -> None:
    """Attaches human-readable structures for explicit file nodes natively."""
    res["size_human"] = format_size(stat.st_size)
    mime, _ = mimetypes.guess_type(path)
    res["mime_type"] = mime or "application/octet-stream"

# ─────────────────────────────────────────────────────────────────────────────
# File IO Drivers
# ─────────────────────────────────────────────────────────────────────────────

async def get_file_info(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
         raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"Path not found: {path}", 404)
         
    stat = os.stat(path)
    is_dir = os.path.isdir(path)
    
    res = {
        "name": os.path.basename(path),
        "type": "directory" if is_dir else "file",
        "size": stat.st_size if not is_dir else None,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat()
    }
    
    if not is_dir:
        _enrich_file_metrics(stat, path, res)
    else:
        res["items_count"] = _get_directory_count(path)
            
    return res

async def rename_path(source: str, target: str) -> None:
    try:
        os.rename(source, target)
    except FileNotFoundError:
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, "Source location node mapped incorrectly.", 404)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to rename sequence dynamically: {str(e)}", 500)

async def copy_path(source: str, target: str) -> None:
    try:
        if os.path.isdir(source):
            await asyncio.to_thread(shutil.copytree, source, target)
        else:
            await asyncio.to_thread(shutil.copy2, source, target)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to copy bytes implicitly: {str(e)}", 500)

async def delete_path(source: str) -> None:
    if not os.path.exists(source):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, "Missing map bindings entirely.", 404)
    try:
        if os.path.isdir(source):
            await asyncio.to_thread(shutil.rmtree, source)
        else:
            os.remove(source)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to detach node directly: {str(e)}", 500)

async def mkdir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Failed to partition structure: {str(e)}", 500)

async def bulk_delete_paths(sources: List[str]) -> List[Dict[str, Any]]:
    results = []
    for source in sources:
        try:
            await delete_path(source)
            results.append({"source": source, "status": "success"})
        except Exception as e:
            results.append({"source": source, "status": "error", "message": str(e)})
    return results

async def bulk_move_paths(operations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    results = []
    for op in operations:
        source, target = op.get("source"), op.get("target")
        if not source or not target:
            results.append({"source": source, "status": "error", "message": "Missing mapped schemas natively."})
            continue
            
        try:
            await rename_path(source, target)
            results.append({"source": source, "target": target, "status": "success"})
        except Exception as e:
            results.append({"source": source, "target": target, "status": "error", "message": str(e)})
    return results
