"""
MCP Session Security Context.

Provides a contextvars-based mechanism to propagate authenticated
identity from the SSE/message endpoints into tool and resource handlers.
This is necessary because the MCP SDK bypasses FastAPI's Depends() injection.
"""
from __future__ import annotations

import contextvars
from typing import Optional

from utils.types import AuthContext

# Holds the authenticated identity for the current MCP request scope.
_current_auth: contextvars.ContextVar[Optional[AuthContext]] = contextvars.ContextVar(
    "_current_auth", default=None
)


def set_mcp_auth(auth: AuthContext) -> None:
    """Stores the resolved auth context for the active MCP session."""
    _current_auth.set(auth)


def get_mcp_auth() -> AuthContext:
    """Retrieves the auth context. Raises if no auth was established."""
    auth = _current_auth.get()
    if auth is None:
        raise RuntimeError("MCP operation attempted without an authenticated session.")
    return auth


def clear_mcp_auth() -> None:
    """Removes the auth context when the session ends."""
    _current_auth.set(None)
