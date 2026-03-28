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

class ConfigManager:
    _instance = None
    _config: Optional[NexusGateConfig] = None
    _config_path: str = ""

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    def load(cls, path: str = "config.toml") -> NexusGateConfig:
        cls._config_path = path

        if not os.path.exists(path):
            logger.info("Config file not found, generating default.", path=path)
            generate_default_config(path)

        with open(path, "rb") as f:
            try:
                toml_dict = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                logger.error("Failed to parse config.toml", error=str(e))
                sys.exit(1)

        try:
            cls._config = NexusGateConfig(**toml_dict)
            logger.info("Config loaded successfully", path=path)
            return cls._config
        except ValidationError as e:
            logger.error("Config validation failed", errors=e.errors())
            sys.exit(1)

    @classmethod
    def get(cls) -> NexusGateConfig:
        if cls._config is None:
            raise RuntimeError("Config not loaded. Call ConfigManager.load() first.")
        return cls._config

    @classmethod
    async def watch(cls):
        """Watch config file for changes and reload."""
        if not cls._config_path:
            return
            
        logger.info("Starting config watcher", path=cls._config_path)
        try:
            async for changes in awatch(cls._config_path):
                logger.info("Config file changed, attempting reload")
                try:
                    with open(cls._config_path, "rb") as f:
                        toml_dict = tomllib.load(f)
                    
                    new_config = NexusGateConfig(**toml_dict)
                    
                    # Store old config to compare what changed
                    old_config = cls._config
                    cls._config = new_config
                    
                    # In a real app we'd dispatch events here for hot-reloadable parts
                    logger.info("Config reloaded successfully")
                except Exception as e:
                    logger.error("Failed to reload config", error=str(e))
        except asyncio.CancelledError:
            logger.info("Config watcher stopped")
