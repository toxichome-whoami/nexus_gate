"""
Storage Tools for MCP.

Exposes NexusGate's file system capabilities as MCP tools.
All paths are resolved and strictly confined within the storage alias root.
"""

from __future__ import annotations

import asyncio
import os

import structlog

from api.mcp.session_auth import get_mcp_auth
from api.mcp.tools.registry import mcp_tool_registry
from config.provider import GlobalConfigProvider
from mcp.types import TextContent

logger = structlog.get_logger()


# -- Path Security ---------------------------------------------------------


def _resolve_safe_path(storage_root: str, user_path: str) -> str | None:
    canonical_root = os.path.realpath(storage_root)
    joined = os.path.join(canonical_root, user_path.lstrip("/"))
    canonical_target = os.path.realpath(joined)

    if (
        not canonical_target.startswith(canonical_root + os.sep)
        and canonical_target != canonical_root
    ):
        return None
    return canonical_target


def _scan_directory(target_dir: str, max_entries: int) -> list[tuple[str, bool]]:
    """Sync helper — runs in a thread. Returns [(name, is_dir), ...]."""
    try:
        raw = os.listdir(target_dir)
    except PermissionError:
        return []

    result = []
    for name in raw[:max_entries]:
        full = os.path.join(target_dir, name)
        try:
            is_dir = os.path.isdir(full)
        except PermissionError:
            is_dir = False
        result.append((name, is_dir))

    has_more = len(raw) > max_entries
    if has_more:
        result.append(("__overflow", len(raw) - max_entries))
    return result


def _read_secure(file_path: str, max_bytes: int, storage_root: str) -> str:
    """Sync helper — runs in a thread. TOCTOU-safe file read."""
    fd = os.open(file_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if st.st_size > max_bytes:
            raise ValueError(f"File too large ({st.st_size:,} bytes). Max: {max_bytes:,}.")

        if hasattr(os, "O_NOFOLLOW") and os.name != "nt":
            actual_path = os.path.realpath(f"/proc/self/fd/{fd}")
            canonical_root = os.path.realpath(storage_root)
            if not actual_path.startswith(canonical_root + os.sep) and actual_path != canonical_root:
                raise PermissionError("Path traversal detected")

        content_bytes = os.read(fd, max_bytes)
        return content_bytes.decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _check_storage_scope(alias: str) -> bool:
    auth = get_mcp_auth()
    if not auth.fs_scope or "*" in auth.fs_scope:
        return True
    return alias in auth.fs_scope


# -- Handlers --------------------------------------------------------------


async def _list_storages() -> list[TextContent]:
    auth = get_mcp_auth()
    config = GlobalConfigProvider().get_config()
    all_aliases = list(config.storage.keys())

    if auth.fs_scope and "*" not in auth.fs_scope:
        visible = [a for a in all_aliases if a in auth.fs_scope]
    else:
        visible = all_aliases

    label = ", ".join(visible) if visible else "None available"
    return [TextContent(type="text", text=f"Available storages: {label}")]


async def _list_files(storage: str, path: str = "/") -> list[TextContent]:
    if not _check_storage_scope(storage):
        return [TextContent(type="text", text="Access denied: the requested resource is not available")]

    config = GlobalConfigProvider().get_config()
    storage_config = config.storage.get(storage)
    if not storage_config:
        return [TextContent(type="text", text=f"Storage '{storage}' not found.")]

    target_dir = _resolve_safe_path(storage_config.path, path)
    if target_dir is None:
        return [TextContent(type="text", text="Access denied: path traversal detected.")]

    is_dir = await asyncio.to_thread(os.path.isdir, target_dir)
    if not is_dir:
        return [TextContent(type="text", text=f"Path '{path}' is not a directory.")]

    max_entries = config.mcp.max_directory_entries
    entries = await asyncio.to_thread(_scan_directory, target_dir, max_entries)

    lines = []
    has_overflow = False
    overflow_count = 0
    for name, is_dir_flag in entries:
        if name == "__overflow":
            has_overflow = True
            overflow_count = is_dir_flag
            continue
        file_type = "[DIR]" if is_dir_flag else "[FILE]"
        lines.append(f"  {file_type:<6} {name}")

    if has_overflow:
        lines.append(f"  ... and {overflow_count} more")

    return [TextContent(type="text", text=f"Contents of '{storage}:{path}':\n" + "\n".join(lines))]


async def _read_file(storage: str, path: str) -> list[TextContent]:
    if not _check_storage_scope(storage):
        return [TextContent(type="text", text="Access denied: the requested resource is not available")]

    config = GlobalConfigProvider().get_config()
    storage_config = config.storage.get(storage)
    if not storage_config:
        return [TextContent(type="text", text=f"Storage '{storage}' not found.")]

    file_path = _resolve_safe_path(storage_config.path, path)
    if file_path is None:
        return [TextContent(type="text", text="Access denied: path traversal detected.")]

    is_file = await asyncio.to_thread(os.path.isfile, file_path)
    if not is_file:
        return [TextContent(type="text", text=f"File '{path}' does not exist.")]

    max_bytes = config.mcp.max_file_read_bytes
    try:
        content = await asyncio.to_thread(_read_secure, file_path, max_bytes, storage_config.path)
        return [TextContent(type="text", text=content)]
    except ValueError as ve:
        return [TextContent(type="text", text=str(ve))]
    except PermissionError:
        return [TextContent(type="text", text="Access denied: path traversal detected.")]
    except Exception:
        return [TextContent(type="text", text="Failed to read file due to an internal error.")]


# -- Registration ----------------------------------------------------------


def register_storage_tools() -> None:
    mcp_tool_registry.register(
        name="list_storages",
        description="Lists all file system directory aliases securely mounted behind NexusGate.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_storages,
    )

    mcp_tool_registry.register(
        name="list_files",
        description="Lists the items inside a given folder inside an allowed storage alias.",
        input_schema={
            "type": "object",
            "properties": {
                "storage": {"type": "string"},
                "path": {"type": "string", "default": "/"},
            },
            "required": ["storage"],
        },
        handler=_list_files,
    )

    mcp_tool_registry.register(
        name="read_file",
        description="Extracts pure text string data locally securely capped to 1MB context limitations.",
        input_schema={
            "type": "object",
            "properties": {"storage": {"type": "string"}, "path": {"type": "string"}},
            "required": ["storage", "path"],
        },
        handler=_read_file,
    )
