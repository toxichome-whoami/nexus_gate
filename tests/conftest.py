import pytest
import sys
import os
import asyncio
import aiosqlite
from pathlib import Path
from fastapi.testclient import TestClient

# ─────────────────────────────────────────────────────────────────────────────
# Environment Initialization
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_src_in_sys_path():
    """Injects the source directory into the path for absolute imports."""
    source_root = os.path.join(os.path.dirname(__file__), "..", "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

_ensure_src_in_sys_path()
os.environ["NEXUSGATE_ENV"] = "test"

# Late imports to ensure sys.path is already updated
from config.loader import ConfigManager
from server.app import create_app

# ─────────────────────────────────────────────────────────────────────────────
# Session Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Manages the lifecycle of a dedicated event loop for the test session."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()

def _create_mock_config_file(tmp_path_factory) -> str:
    """Generates a transient TOML configuration for testing environments."""
    config_dir = tmp_path_factory.mktemp("config")
    config_file = config_dir / "config.toml"
    
    config_file.write_text(
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
    return str(config_file)

@pytest.fixture(scope="session", autouse=True)
def init_config(tmp_path_factory):
    """Bootstraps the global configuration manager with mock data."""
    mock_config_path = _create_mock_config_file(tmp_path_factory)
    ConfigManager.load(mock_config_path)

@pytest.fixture(scope="session")
def app_instance(init_config):
    """Produces a fresh FastAPI application instance configured for testing."""
    return create_app()

@pytest.fixture(scope="session")
def test_client(app_instance):
    """Provides a synchronous test client for API interaction testing."""
    with TestClient(app_instance) as client:
        yield client

# ─────────────────────────────────────────────────────────────────────────────
# Database Fixtures
# ─────────────────────────────────────────────────────────────────────────────

async def _provision_test_schema(db_path: Path):
    """Executes DDL and initial DML for a clean SQLite state."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active BOOLEAN)")
        await db.execute("INSERT INTO users (name, active) VALUES ('Alice', 1), ('Bob', 0)")
        await db.commit()

@pytest.fixture(scope="function")
async def temp_sqlite_db(tmp_path):
    """Creates and tears down a isolated SQLite database for individual tests."""
    db_path = tmp_path / "test.db"
    await _provision_test_schema(db_path)
    
    try:
        yield db_path
    finally:
        if db_path.exists():
            os.remove(db_path)
