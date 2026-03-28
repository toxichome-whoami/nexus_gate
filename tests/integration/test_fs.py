import pytest
from src.main import app
from src.server.middleware.auth import get_auth_context
from src.utils.types import AuthContext, ServerMode

def test_fs_path_traversal(test_client):
    # Mock auth
    def override_auth():
        return AuthContext(api_key_name="test_admin", mode=ServerMode.READWRITE, db_scope=["*"], fs_scope=["*"])
        
    app.dependency_overrides[get_auth_context] = override_auth
    
    # Try downloading a file using path traversal
    # It should hit the validation in handlers and 400 or 404 cleanly
    response = test_client.get("/api/fs/local_fs/download?path=../../etc/passwd")
    
    # The WAF might catch it first (400) or handlers might catch it (400/404)
    assert response.status_code in [400, 403, 404]
    
    app.dependency_overrides.clear()
