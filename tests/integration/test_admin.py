"""Integration tests for the admin API endpoints."""
import pytest
from src.server.middleware.auth import get_auth_context, require_admin
from src.utils.types import AuthContext, ServerMode


def admin_override():
    return AuthContext(
        api_key_name="admin",
        mode=ServerMode.READWRITE,
        db_scope=["*"],
        fs_scope=["*"],
        rate_limit_override=0,
    )


def test_list_bans_empty(test_client):
    from src.main import app
    app.dependency_overrides[get_auth_context] = admin_override
    app.dependency_overrides[require_admin] = admin_override

    response = test_client.get("/api/admin/bans")
    assert response.status_code == 200
    data = response.json()
    assert "ip_bans" in data["data"]
    assert "key_bans" in data["data"]

    app.dependency_overrides.clear()


def test_ban_and_unban_ip(test_client):
    from src.main import app
    app.dependency_overrides[get_auth_context] = admin_override
    app.dependency_overrides[require_admin] = admin_override

    # Ban
    response = test_client.post(
        "/api/admin/bans/ip",
        json={"ip": "1.2.3.4", "reason": "Test ban", "duration_seconds": 3600},
    )
    assert response.status_code == 200
    assert response.json()["data"]["banned_ip"] == "1.2.3.4"

    # Verify it appears in ban list
    bans = test_client.get("/api/admin/bans").json()
    assert "1.2.3.4" in bans["data"]["ip_bans"]

    # Unban
    response = test_client.delete("/api/admin/bans/ip/1.2.3.4")
    assert response.status_code == 200

    # Verify it's gone
    bans = test_client.get("/api/admin/bans").json()
    assert "1.2.3.4" not in bans["data"]["ip_bans"]

    app.dependency_overrides.clear()


def test_circuit_breaker_list(test_client):
    from src.main import app
    app.dependency_overrides[get_auth_context] = admin_override
    app.dependency_overrides[require_admin] = admin_override

    response = test_client.get("/api/admin/circuit-breakers")
    assert response.status_code == 200
    assert "circuits" in response.json()["data"]

    app.dependency_overrides.clear()


def test_view_config_masks_secrets(test_client):
    from src.main import app
    app.dependency_overrides[get_auth_context] = admin_override
    app.dependency_overrides[require_admin] = admin_override

    response = test_client.get("/api/admin/config")
    assert response.status_code == 200
    config_data = response.json()["data"]["config"]

    # Secrets must be redacted
    for key_name, key_data in config_data.get("api_key", {}).items():
        assert key_data["secret"] == "***REDACTED***"

    app.dependency_overrides.clear()
