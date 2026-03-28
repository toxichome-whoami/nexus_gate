import pytest
import tempfile
import os
from pydantic import ValidationError

from src.config.loader import ConfigManager
from src.config.schema import NexusGateConfig

def test_config_load_defaults():
    # Attempt to generate string or defaults
    from src.config.defaults import generate_default_config
    
    default_toml = generate_default_config()
    assert "[server]" in default_toml
    assert "[database.main_db]" in default_toml

def test_config_validation_failures():
    # Invalid memory size
    with pytest.raises(ValidationError):
        NexusGateConfig(**{
            "server": {"host": "0.0.0.0", "port": 4500},
            "cache": {"enabled": True, "max_memory": "invalid_size"}
        })

def test_config_hot_reload_mocking(monkeypatch):
    mgr = ConfigManager()
    
    with tempfile.NamedTemporaryFile(delete=False, mode="w") as f:
        f.write('[server]\nhost="127.0.0.1"\nport=8080\n')
        f.flush()
        
        cfg = mgr.load(f.name)
        assert cfg.server.port == 8080
        
        # In a real environment watchfiles handles the reload via the event loop
        # We just test parser logic here
        
    os.remove(f.name)
