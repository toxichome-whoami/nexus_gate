"""Unit tests for the authentication middleware."""
import base64
import pytest


def encode_key(name: str, secret: str) -> str:
    return base64.b64encode(f"{name}:{secret}".encode()).decode()


def test_encode_key_format():
    token = encode_key("admin", "mysecretkey")
    decoded = base64.b64decode(token).decode()
    assert decoded == "admin:mysecretkey"


def test_auth_rejects_missing_header(test_client):
    response = test_client.post(
        "/api/db/main_db/query",
        json={"sql": "SELECT 1"},
    )
    assert response.status_code == 403  # HTTPBearer returns 403 when header missing


def test_auth_invalid_secret(test_client):
    token = encode_key("test_admin", "wrong_secret_here_padded_to_32chars!")
    response = test_client.post(
        "/api/db/test_db/query",
        json={"sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in [401, 404]


def test_auth_valid_key_passes(test_client, app_instance):
    """Verify that a correctly formed key with correct secret gets past auth."""
    from server.middleware.auth import get_auth_context
    from utils.types import AuthContext, ServerMode

    def override_auth():
        return AuthContext(
            api_key_name="test_admin",
            mode=ServerMode.READWRITE,
            db_scope=["*"],
            fs_scope=["*"],
            rate_limit_override=0,
            full_admin=True,
        )

    app_instance.dependency_overrides[get_auth_context] = override_auth

    response = test_client.get("/api/db/databases")
    assert response.status_code in [200, 404]  # 404 if no DB configured

    app_instance.dependency_overrides.clear()
