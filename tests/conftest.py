import pytest
import sys
import os
import asyncio
import aiosqlite
from pathlib import Path

# Add src/ to the Python path so imports like `from config.loader` work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["NEXUSGATE_ENV"] = "test"

from config.loader import ConfigManager
from server.app import create_app
from security.storage import SecurityStorage

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session", autouse=True)
def init_config(tmp_path_factory):
    """Load a minimal valid config for all tests."""
    config_dir = tmp_path_factory.mktemp("config")
    config_path = config_dir / "config.toml"
    config_path.write_text(
        '[server]\n'
        'host = "127.0.0.1"\n'
        'port = 14500\n'
        '\n'
        '[api_key.test_admin]\n'
        'secret = "your_secret_key_here"\n'
        'mode = "readwrite"\n'
        'db_scope = ["*"]\n'
        'fs_scope = ["*"]\n'
        'full_admin = true\n'
    )
    ConfigManager.load(str(config_path))

@pytest.fixture(scope="session")
def app_instance(init_config):
    """Create and return the FastAPI app instance."""
    return create_app()

@pytest.fixture(scope="session")
def test_client(app_instance):
    from fastapi.testclient import TestClient
    with TestClient(app_instance) as client:
        yield client

@pytest.fixture(scope="function")
async def temp_sqlite_db(tmp_path):
    """Provides a temporary, clean sqlite database."""
    db_path = tmp_path / "test.db"

    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active BOOLEAN)")
        await db.execute("INSERT INTO users (name, active) VALUES ('Alice', 1), ('Bob', 0)")
        await db.commit()

    yield db_path

    if db_path.exists():
        os.remove(db_path)
