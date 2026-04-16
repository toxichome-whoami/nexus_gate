"""Integration tests for the authentication and authorization enforcement layers."""
import base64
import pytest
from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode

# ─────────────────────────────────────────────────────────────────────────────
# Credentials Generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_bearer_header(key_name: str, key_secret: str) -> dict:
    """Produces a standard Authorization header dictionary."""
    raw_payload = f"{key_name}:{key_secret}"
    b64_token = base64.b64encode(raw_payload.encode()).decode()
    return {"Authorization": f"Bearer {b64_token}"}

# ─────────────────────────────────────────────────────────────────────────────
# Endpoint Protection Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_gateway_rejects_unmapped_api_keys(test_client):
    """Ensure that unknown or fictitious credentials result in immediate rejection."""
    headers = _generate_bearer_header("ghost_user", "padded_secret_to_32chars_required")
    response = test_client.get("/api/db/databases", headers=headers)
    
    assert response.status_code == 401

def test_gateway_rejects_missing_authorization(test_client):
    """Confirm that the middleware fails closed when no headers are provided."""
    response = test_client.get("/api/db/databases")
    
    # HTTPBearer returns 403 Forbidden for missing Bearer headers
    assert response.status_code == 403

def test_gateway_enforces_admin_scope_for_protected_routes(test_client, app_instance):
    """Verify that a legitimate non-admin key is blocked from administrative modules."""
    
    def _create_readonly_normal_context():
        """Creates an identity without administrative privileges."""
        return AuthContext(
            api_key_name="standard_readonly_user",
            mode=ServerMode.READONLY,
            db_scope=["*"],
            fs_scope=["*"],
            rate_limit_override=0,
            full_admin=False,
        )

    app_instance.dependency_overrides[get_auth_context] = _create_readonly_normal_context

    try:
        # Routes under /api/admin/ require 'full_admin=True'
        response = test_client.get("/api/admin/keys")
        assert response.status_code == 403
    finally:
        app_instance.dependency_overrides.clear()
