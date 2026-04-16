"""Unit tests for the authentication middleware layer."""
import base64
import pytest
from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode

# ─────────────────────────────────────────────────────────────────────────────
# Test Utilities
# ─────────────────────────────────────────────────────────────────────────────

def create_bearer_token(name: str, secret: str) -> str:
    """Encodes credentials into a Base64-encoded Bearer token."""
    credentials = f"{name}:{secret}"
    return base64.b64encode(credentials.encode()).decode()

def _mock_readwrite_admin() -> AuthContext:
    """Provides a full-admin read-write authentication context."""
    return AuthContext(
        api_key_name="test_admin",
        mode=ServerMode.READWRITE,
        db_scope=["*"],
        fs_scope=["*"],
        rate_limit_override=0,
        full_admin=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Format Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_token_encoding_integrity():
    """Verify that credentials can be correctly encoded and decoded."""
    token = create_bearer_token("admin", "mysecretkey")
    decoded_value = base64.b64decode(token).decode()
    assert decoded_value == "admin:mysecretkey"

# ─────────────────────────────────────────────────────────────────────────────
# Request Authorization Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_authorization_rejects_anonymous_requests(test_client):
    """Ensure that requests without an Authorization header are blocked (403)."""
    response = test_client.post(
        "/api/db/main_db/query",
        json={"sql": "SELECT 1"}
    )
    assert response.status_code == 403

def test_authorization_rejects_invalid_credentials(test_client):
    """Verify that malformed or incorrect secrets result in rejection."""
    invalid_token = create_bearer_token("test_admin", "wrong_secret_value")
    response = test_client.post(
        "/api/db/test_db/query",
        json={"sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {invalid_token}"}
    )
    # The system returns 401 for bad secrets or 404 if the key isn't found
    assert response.status_code in (401, 404)

def test_authenticated_key_passes_authorization(test_client, app_instance):
    """Verify that a valid authentication context allows access to protected routes."""
    app_instance.dependency_overrides[get_auth_context] = _mock_readwrite_admin

    try:
        response = test_client.get("/api/db/databases")
        # 404 is acceptable if no DBs exist, but 200 is expected if they do.
        # Both indicate auth succeeded.
        assert response.status_code in (200, 404)
    finally:
        app_instance.dependency_overrides.clear()
