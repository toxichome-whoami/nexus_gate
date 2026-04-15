import os
import sys
import tomllib
import asyncio
from typing import Optional
from watchfiles import awatch
import structlog
from pydantic import ValidationError

from config.schema import NexusGateConfig
from config.defaults import generate_default_config

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Helper Procedures
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_file_exists(path: str) -> None:
    """Forces generation of scaffolding structure natively if absent."""
    if not os.path.exists(path):
        logger.info("Config file not found, generating default.", path=path)
        generate_default_config(path)

def _parse_toml_file(path: str) -> dict:
    """Safely decodes raw disk bytes preventing corrupted config structures."""
    try:
        with open(path, "rb") as file:
            return tomllib.load(file)
    except tomllib.TOMLDecodeError as toml_error:
        logger.error("Failed to parse config.toml syntax", error=str(toml_error))
        sys.exit(1)

def _validate_schema(config_dict: dict, path: str) -> NexusGateConfig:
    """Applies strict Pydantic parsing ensuring zero runtime mapping failures."""
    try:
        validated_config = NexusGateConfig(**config_dict)
        logger.info("Config loaded successfully", path=path)
        return validated_config
    except ValidationError as strict_error:
        logger.error("Config schema validation failed", errors=strict_error.errors())
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Core Loader Class
# ─────────────────────────────────────────────────────────────────────────────

class ConfigManager:
    """Acts as a global memory singleton holding validated configurations."""
    _instance = None
    _config: Optional[NexusGateConfig] = None
    _config_path: str = ""

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    def load(cls, path: str = "config.toml") -> NexusGateConfig:
        """Hydrates the singleton from local variables sequentially."""
        cls._config_path = path
        
        _ensure_file_exists(path)
        config_payload = _parse_toml_file(path)
        
        cls._config = _validate_schema(config_payload, path)
        return cls._config

    @classmethod
    def get(cls) -> NexusGateConfig:
        """Retrieves active schema implicitly boosting reliability on missed injects."""
        if cls._config is None:
            return cls.load()
        return cls._config

    @classmethod
    async def watch(cls):
        """Asynchronously monitors target targets for hot-reloads dynamically."""
        if not cls._config_path:
            return
            
        logger.info("Starting config watcher daemon", path=cls._config_path)
        
        try:
            async for _ in awatch(cls._config_path):
                logger.info("Config file modification detected, refreshing")
                cls._handle_hot_reload()
                
        except asyncio.CancelledError:
            logger.info("Config watcher daemon stopped gracefully")

    @classmethod
    def _handle_hot_reload(cls):
        """Attempts isolated validation bypass of new file state before replacing memory."""
        try:
            new_payload = _parse_toml_file(cls._config_path)
            new_validated = NexusGateConfig(**new_payload)
            
            # old_config = cls._config  # Accessible for diffing later if needed
            cls._config = new_validated
            logger.info("Config hot-reloaded successfully on-the-fly")
        except Exception as runtime_error:
            logger.error("Failed to hot-reload configuration file", error=str(runtime_error))
