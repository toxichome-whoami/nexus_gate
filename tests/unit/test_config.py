import pytest
import tempfile
import os
from config.loader import ConfigManager
from config.schema import NexusGateConfig

# ─────────────────────────────────────────────────────────────────────────────
# Schema Validation Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_config_schema_default_values():
    """Verify that NexusGateConfig initializes with strict architectural defaults."""
    config = NexusGateConfig()
    
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 4500
    assert config.features.database is True

# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle & Persistence Tests
# ─────────────────────────────────────────────────────────────────────────────

def _create_temporary_config(port: int = 8080) -> str:
    """Helper to generate a transient configuration file for IO testing."""
    handle, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(handle, 'w') as f:
        f.write(
            f'[server]\nhost="127.0.0.1"\nport={port}\n\n'
            '[api_key.reload_test]\n'
            'secret = "your_secret_key_here"\n'
            'mode = "readwrite"\n'
        )
    return path

def test_config_loading_from_filesystem():
    """Verify that the ConfigManager correctly parses and projects TOML structures."""
    config_path = _create_temporary_config(port=8080)
    
    try:
        loaded_config = ConfigManager.load(config_path)
        assert loaded_config.server.port == 8080
    finally:
        if os.path.exists(config_path):
            os.remove(config_path)
