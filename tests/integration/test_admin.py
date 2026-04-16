"""Integration tests for the administrative API surface."""
import pytest
from server.middleware.auth import get_auth_context, require_admin
from utils.types import AuthContext, ServerMode

# ─────────────────────────────────────────────────────────────────────────────
# Auth Overrides
# ─────────────────────────────────────────────────────────────────────────────

def _create_admin_context() -> AuthContext:
    """Produces a privileged authentication identity for bypass testing."""
    return AuthContext(
        api_key_name="test_admin",
        mode=ServerMode.READWRITE,
        db_scope=["*"],
        fs_scope=["*"],
        rate_limit_override=0,
        full_admin=True,
    )

def _enable_admin_access(app_instance):
    """Binds admin-level scopes to the dependency injection container."""
    identity = _create_admin_context()
    app_instance.dependency_overrides[get_auth_context] = lambda: identity
    app_instance.dependency_overrides[require_admin] = lambda: identity

# ─────────────────────────────────────────────────────────────────────────────
# Ban Management Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_fetch_bans_returns_schema(test_client, app_instance):
    """Verify that the ban list endpoint returns correctly structured empty lists."""
    _enable_admin_access(app_instance)

    try:
        response = test_client.get("/api/admin/bans")
        assert response.status_code == 200
        
        payload = response.json()["data"]
        assert "ip_bans" in payload and "key_bans" in payload
    finally:
        app_instance.dependency_overrides.clear()

def test_admin_ip_ban_lifecycle(test_client, app_instance):
    """Ensure IPs can be dynamically banned and subsequently permitted."""
    _enable_admin_access(app_instance)
    target_ip = "1.2.3.4"

    try:
        # Step 1: Execute Ban
        ban_response = test_client.post(
            "/api/admin/bans/ip",
            json={"ip": target_ip, "reason": "Integration Test", "duration_seconds": 3600}
        )
        assert ban_response.status_code == 200
        assert ban_response.json()["data"]["banned_ip"] == target_ip

        # Step 2: Verify Persistence
        bans = test_client.get("/api/admin/bans").json()["data"]
        assert target_ip in bans["ip_bans"]

        # Step 3: Revoke Ban
        revoke_response = test_client.delete(f"/api/admin/bans/ip/{target_ip}")
        assert revoke_response.status_code == 200

        # Step 4: Verify Removal
        final_bans = test_client.get("/api/admin/bans").json()["data"]
        assert target_ip not in final_bans["ip_bans"]
    finally:
        app_instance.dependency_overrides.clear()

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Metrics Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_fetch_circuit_breaker_states(test_client, app_instance):
    """Verify visibility of circuit breaker health metrics."""
    _enable_admin_access(app_instance)

    try:
        response = test_client.get("/api/admin/circuit-breakers")
        assert response.status_code == 200
        assert "circuits" in response.json()["data"]
    finally:
        app_instance.dependency_overrides.clear()

def test_admin_config_view_redacts_sensitive_tokens(test_client, app_instance):
    """Confirm that the configuration echo route obscures raw API secrets."""
    _enable_admin_access(app_instance)

    try:
        response = test_client.get("/api/admin/config")
        assert response.status_code == 200
        
        api_keys = response.json()["data"]["config"].get("api_key", {})
        for metadata in api_keys.values():
            assert metadata["secret"] == "***REDACTED***"
    finally:
        app_instance.dependency_overrides.clear()

# ─────────────────────────────────────────────────────────────────────────────
# Key Lifecycle Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_generate_and_revoke_ephemeral_key(test_client, app_instance):
    """Verify that sub-keys can be provisioned and immediately invalidated."""
    _enable_admin_access(app_instance)
    temp_key_name = "test_dynamic_key"

    try:
        # Step 1: Provision
        provision_res = test_client.post(
            "/api/admin/keys",
            json={"name": temp_key_name, "mode": "readonly", "db_scope": ["*"]}
        )
        assert provision_res.status_code == 200
        assert provision_res.json()["data"]["name"] == temp_key_name

        # Step 2: Verify Scopes
        key_list = test_client.get("/api/admin/keys").json()["data"]["keys"]
        target_meta = next(k for k in key_list if k["name"] == temp_key_name)
        assert target_meta["full_admin"] is False

        # Step 3: Invalidate
        revoke_res = test_client.delete(f"/api/admin/keys/{temp_key_name}")
        assert revoke_res.status_code == 200
    finally:
        app_instance.dependency_overrides.clear()

# ─────────────────────────────────────────────────────────────────────────────
# Safety Guard Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_prevents_self_lockout(test_client, app_instance):
    """Ensure that the currently active admin identity cannot ban its own access."""
    _enable_admin_access(app_instance)

    try:
        # Attempt self-ban
        ban_attempt = test_client.post(
            "/api/admin/bans/key",
            json={"key_name": "test_admin", "reason": "self-lockout test"}
        )
        assert ban_attempt.status_code == 400

        # Attempt self-revocation
        revoke_attempt = test_client.delete("/api/admin/keys/test_admin")
        assert revoke_attempt.status_code == 400
    finally:
        app_instance.dependency_overrides.clear()
