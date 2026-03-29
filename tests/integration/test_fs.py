import pytest

from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode


def test_fs_path_traversal(test_client, app_instance):
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

    # Try downloading a file using path traversal
    response = test_client.get("/api/fs/local_fs/download?path=../../etc/passwd")

    # The WAF might catch it first (400) or handlers might catch it (400/404)
    assert response.status_code in [400, 403, 404]

    app_instance.dependency_overrides.clear()
