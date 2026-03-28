import pytest
import os
import aiosqlite
from fastapi.testclient import TestClient
from pathlib import Path

# Important: Set up env vars or overrides before main is imported
os.environ["NEXUSGATE_ENV"] = "test"

from src.main import app 
from src.config.loader import ConfigManager

@pytest.fixture(scope="session")
def test_client():
    # Force a dummy config for tests to avoid hitting production resources
    config = ConfigManager.get()
    
    # We could dynamically patch config here
    
    with TestClient(app) as client:
        yield client

@pytest.fixture(scope="function")
async def temp_sqlite_db(tmp_path):
    """Provides a temporary, clean sqlite database."""
    db_path = tmp_path / "test.db"
    
    # Init basic tables for test
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active BOOLEAN)")
        await db.execute("INSERT INTO users (name, active) VALUES ('Alice', 1), ('Bob', 0)")
        await db.commit()
        
    yield db_path
    
    if db_path.exists():
        os.remove(db_path)
