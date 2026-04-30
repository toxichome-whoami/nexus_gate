"""
Storage Tools for MCP.

Exposes NexusGate's file system capabilities as MCP tools.
All paths are resolved and strictly confined within the storage alias root.
"""

from __future__ import annotations

import os

import structlog

from api.mcp.session_auth import get_mcp_auth
from api.mcp.tools.base import MAX_DIRECTORY_ENTRIES, MAX_FILE_READ_BYTES
from api.mcp.tools.registry import mcp_tool_registry
from config.loader import ConfigManager
from mcp.types import TextContent

logger = structlog.get_logger()


# -- Path Security ---------------------------------------------------------


def _resolve_safe_path(storage_root: str, user_path: str) -> str | None:
    """
    Resolves the user-supplied path against the storage root and verifies
    the result is still within the root. Returns None if the resolved path
    escapes the allowed directory (path traversal attempt).
    """
    # Normalize the root to an absolute, symlink-resolved canonical form
    canonical_root = os.path.realpath(storage_root)

    # Build and resolve the target path
    joined = os.path.join(canonical_root, user_path.lstrip("/"))
    canonical_target = os.path.realpath(joined)

    # Verify containment: the resolved target must start with the root
    if (
        not canonical_target.startswith(canonical_root + os.sep)
        and canonical_target != canonical_root
    ):
        return None

    return canonical_target


def _check_storage_scope(alias: str) -> bool:
    """Verifies the authenticated session has access to the requested storage alias."""
    auth = get_mcp_auth()
    # Empty fs_scope or "*" means unrestricted access to all storages
    if not auth.fs_scope or "*" in auth.fs_scope:
        return True
    return alias in auth.fs_scope


# -- Handlers --------------------------------------------------------------


async def _list_storages() -> list[TextContent]:
    """Lists storage aliases visible to the authenticated session."""
    auth = get_mcp_auth()
    all_aliases = list(ConfigManager.get().storage.keys())

    # Filter by scope if restrictions exist and it's not a global wildcard
    if auth.fs_scope and "*" not in auth.fs_scope:
        visible = [a for a in all_aliases if a in auth.fs_scope]
    else:
        visible = all_aliases

    label = ", ".join(visible) if visible else "None available"
    return [TextContent(type="text", text=f"Available storages: {label}")]


async def _list_files(storage: str, path: str = "/") -> list[TextContent]:
    if not _check_storage_scope(storage):
        return [
            TextContent(
                type="text",
                text=f"Access denied: storage '{storage}' is outside your scope.",
            )
        ]

    storage_config = ConfigManager.get().storage.get(storage)
    if not storage_config:
        return [TextContent(type="text", text=f"Storage '{storage}' not found.")]

    target_dir = _resolve_safe_path(storage_config.path, path)
    if target_dir is None:
        return [
            TextContent(type="text", text="Access denied: path traversal detected.")
        ]

    if not os.path.isdir(target_dir):
        return [TextContent(type="text", text=f"Path '{path}' is not a directory.")]

    raw_entries = os.listdir(target_dir)
    lines = []
    for name in raw_entries[:MAX_DIRECTORY_ENTRIES]:
        full = os.path.join(target_dir, name)
        file_type = "[DIR]" if os.path.isdir(full) else "[FILE]"
        lines.append(f"  {file_type:<6} {name}")

    overflow = len(raw_entries) - MAX_DIRECTORY_ENTRIES
    if overflow > 0:
        lines.append(f"  ... and {overflow} more")

    return [
        TextContent(
            type="text", text=f"Contents of '{storage}:{path}':\n" + "\n".join(lines)
        )
    ]


async def _read_file(storage: str, path: str) -> list[TextContent]:
    if not _check_storage_scope(storage):
        return [
            TextContent(
                type="text",
                text=f"Access denied: storage '{storage}' is outside your scope.",
            )
        ]

    storage_config = ConfigManager.get().storage.get(storage)
    if not storage_config:
        return [TextContent(type="text", text=f"Storage '{storage}' not found.")]

    file_path = _resolve_safe_path(storage_config.path, path)
    if file_path is None:
        return [
            TextContent(type="text", text="Access denied: path traversal detected.")
        ]

    if not os.path.isfile(file_path):
        return [TextContent(type="text", text=f"File '{path}' does not exist.")]

    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_READ_BYTES:
        return [TextContent(type="text", text=f"File too large ({file_size:,} bytes).")]

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        return [TextContent(type="text", text=content)]
    except Exception:
        return [
            TextContent(
                type="text", text="Failed to read file due to an internal error."
            )
        ]


# -- Registration ----------------------------------------------------------


def register_storage_tools() -> None:
    """Registers storage capabilities into the global tool registry."""

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
