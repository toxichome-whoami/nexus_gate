"""Integration tests for the admin API endpoints."""
import pytest
from server.middleware.auth import get_auth_context, require_admin
from utils.types import AuthContext, ServerMode


def admin_override():
    return AuthContext(
        api_key_name="test_admin",
        mode=ServerMode.READWRITE,
        db_scope=["*"],
        fs_scope=["*"],
        rate_limit_override=0,
        full_admin=True,
    )


def test_list_bans_empty(test_client, app_instance):
    app_instance.dependency_overrides[get_auth_context] = admin_override
    app_instance.dependency_overrides[require_admin] = admin_override

    response = test_client.get("/api/admin/bans")
    assert response.status_code == 200
    data = response.json()
    assert "ip_bans" in data["data"]
    assert "key_bans" in data["data"]

    app_instance.dependency_overrides.clear()


def test_ban_and_unban_ip(test_client, app_instance):
    app_instance.dependency_overrides[get_auth_context] = admin_override
    app_instance.dependency_overrides[require_admin] = admin_override

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

    app_instance.dependency_overrides.clear()


def test_circuit_breaker_list(test_client, app_instance):
    app_instance.dependency_overrides[get_auth_context] = admin_override
    app_instance.dependency_overrides[require_admin] = admin_override

    response = test_client.get("/api/admin/circuit-breakers")
    assert response.status_code == 200
    assert "circuits" in response.json()["data"]

    app_instance.dependency_overrides.clear()


def test_view_config_masks_secrets(test_client, app_instance):
    app_instance.dependency_overrides[get_auth_context] = admin_override
    app_instance.dependency_overrides[require_admin] = admin_override

    response = test_client.get("/api/admin/config")
    assert response.status_code == 200
    config_data = response.json()["data"]["config"]

    # Secrets must be redacted
    for key_name, key_data in config_data.get("api_key", {}).items():
        assert key_data["secret"] == "***REDACTED***"

    app_instance.dependency_overrides.clear()


def test_create_and_revoke_dynamic_key(test_client, app_instance):
    app_instance.dependency_overrides[get_auth_context] = admin_override
    app_instance.dependency_overrides[require_admin] = admin_override

    # Create
    response = test_client.post(
        "/api/admin/keys",
        json={"name": "test_dynamic_key", "mode": "readonly", "db_scope": ["*"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "test_dynamic_key"
    assert "secret" in data
    assert "bearer_token" in data

    # Verify it appears in list
    keys = test_client.get("/api/admin/keys").json()["data"]["keys"]
    names = [k["name"] for k in keys]
    assert "test_dynamic_key" in names

    # Dynamic key should never have full_admin
    dynamic = [k for k in keys if k["name"] == "test_dynamic_key"][0]
    assert dynamic["full_admin"] is False

    # Revoke
    response = test_client.delete("/api/admin/keys/test_dynamic_key")
    assert response.status_code == 200

    app_instance.dependency_overrides.clear()


def test_self_lockout_prevention(test_client, app_instance):
    app_instance.dependency_overrides[get_auth_context] = admin_override
    app_instance.dependency_overrides[require_admin] = admin_override

    # Try to ban own key
    response = test_client.post(
        "/api/admin/bans/key",
        json={"key_name": "test_admin", "reason": "self-test"},
    )
    assert response.status_code == 400

    # Try to revoke own key
    response = test_client.delete("/api/admin/keys/test_admin")
    assert response.status_code == 400

    app_instance.dependency_overrides.clear()
