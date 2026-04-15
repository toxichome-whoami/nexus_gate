"""Integration tests for the database API layer and system health status."""
import pytest
from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode

# ─────────────────────────────────────────────────────────────────────────────
# Global Infrastructure Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_heartbeat_health_check_status(test_client):
    """Verify that the system health indicator is reachable and reports success."""
    response = test_client.get("/health")
    assert response.status_code == 200
    
    payload = response.json()
    assert payload["success"] is True
    assert "status" in payload["data"]

# ─────────────────────────────────────────────────────────────────────────────
# Access Control Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_database_queries_require_mandatory_auth(test_client):
    """Ensure that the SQL query gateway is protected by basic authentication checks."""
    response = test_client.post(
        "/api/db/any_database/query",
        json={"sql": "SELECT 1"}
    )
    # Rejection by auth middleware
    assert response.status_code in (401, 403)

def test_database_router_with_elevated_bypass(test_client, app_instance):
    """Confirm the routing flow when credentials are valid but target resources are transient."""
    
    def _inject_root_context():
        """Provides a bypass context for the authentication handler."""
        return AuthContext(
            api_key_name="test_superuser",
            mode=ServerMode.READWRITE,
            db_scope=["*"],
            fs_scope=["*"],
            rate_limit_override=0,
            full_admin=True,
        )

    app_instance.dependency_overrides[get_auth_context] = _inject_root_context

    try:
        # Step: Attempt a mutation on a likely missing database
        # Even if security allows it, the engine should report a failure gracefully
        response = test_client.post(
            "/api/db/non_existent_target/query",
            json={"sql": "DROP TABLE dummy_table"}
        )

        # Valid failure modes: 
        # 404 (DB Not Found), 400 (Bad Implementation), 500 (Unhandled Engine Failure)
        assert response.status_code in (400, 403, 404, 500)
    finally:
        app_instance.dependency_overrides.clear()
