"""Integration tests for the authentication middleware and endpoints."""
import pytest
import base64

from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode


def encode_key(name: str, secret: str) -> str:
    return base64.b64encode(f"{name}:{secret}".encode()).decode()


def test_auth_integration_invalid_key(test_client):
    token = encode_key("invalid_user", "some_secret_padded_to_32chars_here!")
    response = test_client.get(
        "/api/db/databases",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_auth_integration_missing_header(test_client):
    response = test_client.get("/api/db/databases")
    assert response.status_code == 403  # HTTPBearer gives 403 on missing


def test_auth_integration_admin_route_blocked_for_normal(test_client, app_instance):
    def override_auth_normal():
        return AuthContext(
            api_key_name="normal_user",
            mode=ServerMode.READONLY,
            db_scope=["*"],
            fs_scope=["*"],
            rate_limit_override=0,
            full_admin=False,
        )

    app_instance.dependency_overrides[get_auth_context] = override_auth_normal

    response = test_client.get("/api/admin/keys")
    # require_admin should block this
    assert response.status_code == 403

    app_instance.dependency_overrides.clear()
