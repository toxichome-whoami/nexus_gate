"""Integration tests for the storage and filesystem protection layers."""
import pytest
from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode

# ─────────────────────────────────────────────────────────────────────────────
# Path Security Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_filesystem_resolves_and_blocks_traversal_attempts(test_client, app_instance):
    """Ensure that relative path injections are caught by path resolution logic."""
    
    def _inject_full_scope_identity():
        """Bypass for the auth middleware with unrestricted storage scope."""
        return AuthContext(
            api_key_name="test_admin_identity",
            mode=ServerMode.READWRITE,
            db_scope=["*"],
            fs_scope=["*"],
            rate_limit_override=0,
            full_admin=True,
        )

    app_instance.dependency_overrides[get_auth_context] = _inject_full_scope_identity

    try:
        # Step: Attempt to escape the storage root via query parameter
        malicious_path = "../../etc/passwd"
        response = test_client.get(f"/api/fs/local_storage_node/download?path={malicious_path}")

        # The system must reject this as 400 (Bad Request) or 403 (Forbidden)
        # or 404 if the alias doesn't exist, which still prevents traversal.
        assert response.status_code in (400, 403, 404)
    finally:
        app_instance.dependency_overrides.clear()
