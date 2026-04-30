import asyncio
import mimetypes
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List

from api.errors import ErrorCodes, NexusGateException
from utils.size_parser import format_size

# ─────────────────────────────────────────────────────────────────────────────
# Extraction Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _enrich_file_metrics(stat, path: str, res: dict) -> None:
    """Attaches human-readable structures for explicit file nodes natively."""
    res["size"] = [stat.st_size, format_size(stat.st_size)]
    mime, _ = mimetypes.guess_type(path)
    res["mime_type"] = mime or "application/octet-stream"


# ─────────────────────────────────────────────────────────────────────────────
# File IO Drivers
# ─────────────────────────────────────────────────────────────────────────────


def _stat_to_file_info(path: str, name: str, stat_result) -> Dict[str, Any]:
    """Build file info dict from an already-obtained stat result — zero extra syscalls."""
    import stat as stat_m

    is_dir = stat_m.S_ISDIR(stat_result.st_mode)

    res: Dict[str, Any] = {
        "name": name,
        "type": "directory" if is_dir else "file",
        "modified": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat_result.st_ctime).isoformat(),
    }

    if not is_dir:
        _enrich_file_metrics(stat_result, path, res)
    else:
        res["items_count"] = 0

    return res


async def get_file_info(path: str) -> Dict[str, Any]:
    """Get file info with blocking I/O offloaded to a threadpool."""
    try:
        stat_result = await asyncio.to_thread(os.stat, path)
    except FileNotFoundError:
        raise NexusGateException(
            ErrorCodes.FS_PATH_NOT_FOUND, f"Path not found: {path}", 404
        )

    name = os.path.basename(path)
    return _stat_to_file_info(path, name, stat_result)


def build_file_info_from_entry(entry: "os.DirEntry[str]") -> Dict[str, Any]:
    """Build file info from a scandir DirEntry — zero syscalls (scandir pre-fetches).
    This is a sync helper meant to be called inside a threadpool or from scandir."""
    try:
        stat_result = entry.stat(follow_symlinks=False)
    except (OSError, PermissionError):
        return {
            "name": entry.name,
            "type": "unknown",
            "size": [0, "0 B"],
            "mime_type": "application/octet-stream",
            "modified": datetime.now().isoformat(),
            "created": datetime.now().isoformat(),
        }

    path = entry.path
    is_dir = entry.is_dir(follow_symlinks=False)

    res: Dict[str, Any] = {
        "name": entry.name,
        "type": "directory" if is_dir else "file",
        "modified": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat_result.st_ctime).isoformat(),
    }

    if not is_dir:
        _enrich_file_metrics(stat_result, path, res)

    return res


async def rename_path(source: str, target: str) -> None:
    try:
        await asyncio.to_thread(os.rename, source, target)
    except FileNotFoundError:
        raise NexusGateException(
            ErrorCodes.FS_PATH_NOT_FOUND,
            "Source location node mapped incorrectly.",
            404,
        )
    except Exception as e:
        raise NexusGateException(
            ErrorCodes.SERVER_INTERNAL,
            f"Failed to rename sequence dynamically: {str(e)}",
            500,
        )


async def copy_path(source: str, target: str) -> None:
    try:
        is_dir = await asyncio.to_thread(os.path.isdir, source)
        if is_dir:
            await asyncio.to_thread(shutil.copytree, source, target)
        else:
            await asyncio.to_thread(shutil.copy2, source, target)
    except Exception as e:
        raise NexusGateException(
            ErrorCodes.SERVER_INTERNAL,
            f"Failed to copy bytes implicitly: {str(e)}",
            500,
        )


async def delete_path(source: str) -> None:
    if not os.path.exists(source):
        raise NexusGateException(
            ErrorCodes.FS_PATH_NOT_FOUND, "Missing map bindings entirely.", 404
        )
    try:
        if os.path.isdir(source):
            await asyncio.to_thread(shutil.rmtree, source)
        else:
            await asyncio.to_thread(os.remove, source)
    except Exception as e:
        raise NexusGateException(
            ErrorCodes.SERVER_INTERNAL, f"Failed to detach node directly: {str(e)}", 500
        )


async def mkdir(path: str) -> None:
    try:
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)
    except Exception as e:
        raise NexusGateException(
            ErrorCodes.SERVER_INTERNAL, f"Failed to partition structure: {str(e)}", 500
        )


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
            results.append(
                {
                    "source": source,
                    "status": "error",
                    "message": "Missing mapped schemas natively.",
                }
            )
            continue

        try:
            await rename_path(source, target)
            results.append({"source": source, "target": target, "status": "success"})
        except Exception as e:
            results.append(
                {
                    "source": source,
                    "target": target,
                    "status": "error",
                    "message": str(e),
                }
            )
    return results
