"""
Storage Resource Provider for MCP.

Exposes storage alias configuration constraints and metrics as MCP resources.

Resource URI Format: nexusgate://fs/{alias}/info
"""

from __future__ import annotations

import structlog
from pydantic import AnyUrl

from api.mcp.resources.registry import build_error_text_resource, mcp_resource_registry
from config.loader import ConfigManager
from mcp.types import Resource, TextResourceContents

logger = structlog.get_logger()

# Constants
URI_PROTOCOL_PREFIX = "nexusgate://fs/"
URI_INFO_SUFFIX = "/info"


# ── Internal Functions ───────────────────────────────────────────────────


def _extract_storage_alias(uri: str) -> str | None:
    """Isolates the custom storage alias string from the requested resource URI."""
    if not uri.startswith(URI_PROTOCOL_PREFIX):
        return None

    if not uri.endswith(URI_INFO_SUFFIX):
        return None

    extracted_alias = uri[len(URI_PROTOCOL_PREFIX) : -len(URI_INFO_SUFFIX)]
    return extracted_alias if extracted_alias else None


async def _compile_storage_info(uri: str, alias: str) -> TextResourceContents:
    """Builds a diagnostic overview reading limits and features logically."""
    storage_config = ConfigManager.get().storage.get(alias)

    if not storage_config:
        return build_error_text_resource(
            uri, f"Storage alias '{alias}' is completely invalid."
        )

    # Convert arrays into readable comma definitions
    blocked_formats = ", ".join(storage_config.blocked_extensions)

    diagnostic_lines = [
        f"# Storage Configuration: {alias}",
        f"- Permission Mode: {storage_config.mode.value}",
        f"- Global Capacity Limit: {storage_config.limit}",
        f"- Max Cap Per File: {storage_config.max_file_size}",
        f"- Streaming Chunk Size: {storage_config.chunk_size}",
        f"- Restricted Formats: {blocked_formats}",
    ]

    return TextResourceContents(
        uri=AnyUrl(uri), mimeType="text/markdown", text="\n".join(diagnostic_lines)
    )


async def _read_storage_info(uri: str) -> list[TextResourceContents]:
    """Top-level reader hook resolving file system metric parameters securely."""
    target_storage_alias = _extract_storage_alias(uri)

    # Guard against malformed requests mapped incorrectly
    if not target_storage_alias:
        return [
            build_error_text_resource(
                uri, "Encountered broken storage Resource URI structure."
            )
        ]

    rendered_context = await _compile_storage_info(uri, target_storage_alias)
    return [rendered_context]


# ── Exported Registration ────────────────────────────────────────────────


def register_storage_resources() -> None:
    """Configures storage parameter definitions natively into the global resource map."""

    active_configurations = ConfigManager.get().storage

    for storage_alias in active_configurations:
        target_uri = f"{URI_PROTOCOL_PREFIX}{storage_alias}{URI_INFO_SUFFIX}"

        static_resource = Resource(
            uri=AnyUrl(target_uri),
            name=f"Storage Limits: {storage_alias}",
            description=f"Global sizes, permission maps, and blocked formats for '{storage_alias}'.",
            mimeType="text/markdown",
        )
        mcp_resource_registry.register_listing(static_resource)

    # Attach the generic storage info processor
    mcp_resource_registry.register_reader_prefix(
        URI_PROTOCOL_PREFIX, _read_storage_info
    )
    logger.debug("Storage configuration resources mapped into registry.")
