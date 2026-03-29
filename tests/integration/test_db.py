import pytest

from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode


def test_health_endpoint(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "status" in data["data"]


def test_db_query_requires_auth(test_client):
    """Sending a query without auth should fail at the auth layer."""
    response = test_client.post(
        "/api/db/main_db/query",
        json={"sql": "SELECT 1"}
    )
    assert response.status_code in [401, 403]


def test_db_query_with_mocked_auth(test_client, app_instance):
    """With mocked auth, dangerous queries should still be blocked or DB not found."""
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

    response = test_client.post(
        "/api/db/main_db/query",
        json={"sql": "DROP TABLE users"}
    )

    # DB Engine will fail because main_db doesn't exist in test config,
    # or security will block the dangerous query
    assert response.status_code in [400, 403, 404, 500]

    app_instance.dependency_overrides.clear()
