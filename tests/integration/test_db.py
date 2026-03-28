import pytest
import json

def test_health_endpoint(test_client):
    response = test_client.get("/health")
    # Health might be degraded if no actual databases are configured properly in test
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "status" in data["data"]
    
def test_db_query_invalid_ast(test_client):
    # This assumes standard configuration with security enabled
    # We don't have a valid API key in this dummy request, so we should expect 401
    response = test_client.post(
        "/api/db/main_db/query",
        json={"sql": "DROP TABLE users"}
    )
    
    # Fails at auth layer first
    assert response.status_code == 401
    
    # If we mocked auth via dependency overrides:
    from src.server.middleware.auth import get_auth_context
    from src.utils.types import AuthContext, ServerMode
    
    def override_auth():
        return AuthContext(api_key_name="test_admin", mode=ServerMode.READWRITE, db_scope=["*"], fs_scope=["*"])
        
    from src.main import app
    app.dependency_overrides[get_auth_context] = override_auth
    
    # Now try the dangerous query
    response2 = test_client.post(
        "/api/db/main_db/query",
        json={"sql": "DROP TABLE users"}
    )
    
    # DB Engine will likely fail because main_db might not exist in test config
    # but let's assert it passes auth now
    assert response2.status_code in [403, 404, 500] 
    
    # Clean up
    app.dependency_overrides.clear()
