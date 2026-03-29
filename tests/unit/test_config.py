import pytest
import tempfile
import os
from pydantic import ValidationError

from config.loader import ConfigManager
from config.schema import NexusGateConfig


def test_config_schema_defaults():
    """NexusGateConfig should produce valid defaults."""
    cfg = NexusGateConfig()
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 4500
    assert cfg.features.database is True


def test_config_hot_reload_mocking():
    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".toml") as f:
        f.write(
            '[server]\nhost="127.0.0.1"\nport=8080\n\n'
            '[api_key.reload_test]\n'
            'secret = "your_secret_key_here"\n'
            'mode = "readwrite"\n'
        )
        f.flush()

        cfg = ConfigManager.load(f.name)
        assert cfg.server.port == 8080

    os.remove(f.name)
