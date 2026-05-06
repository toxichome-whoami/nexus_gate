import asyncio
import os
import sys
import tomllib
from typing import Optional

import structlog
from pydantic import ValidationError
from watchfiles import awatch

from config.defaults import generate_default_config
from config.schema import NexusGateConfig

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Helper Procedures
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_file_exists(path: str) -> None:
    """Forces generation of scaffolding structure natively if absent."""
    if not os.path.exists(path):
        logger.info("Config file not found, generating default.", path=path)
        generate_default_config(path)


def _parse_toml_file(path: str, exit_on_error: bool = True) -> dict:
    """Safely decodes raw disk bytes preventing corrupted config structures."""
    try:
        with open(path, "rb") as file:
            return tomllib.load(file)
    except tomllib.TOMLDecodeError as toml_error:
        if exit_on_error:
            logger.error("Failed to parse config.toml syntax", error=str(toml_error))
            sys.exit(1)
        raise toml_error


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

        config_dir = os.path.dirname(os.path.abspath(cls._config_path))
        target_file = os.path.basename(cls._config_path)

        logger.info("Starting config watcher daemon", path=cls._config_path)

        try:
            async for changes in awatch(config_dir):
                for change, path in changes:
                    if os.path.basename(path) == target_file:
                        logger.info("Config file modification detected, refreshing")
                        await cls._handle_hot_reload()
                        break

        except asyncio.CancelledError:
            logger.info("Config watcher daemon stopped gracefully")

    @classmethod
    async def _handle_hot_reload(cls):
        """Attempts isolated validation bypass of new file state before replacing memory."""
        try:
            new_payload = await asyncio.to_thread(_parse_toml_file, cls._config_path, False)
            new_validated = NexusGateConfig(**new_payload)

            cls._config = new_validated

            # Refresh module-level feature flags in dependent modules
            try:
                import api.database.handlers as _dbh
                _dbh._refresh_feature_flags()
            except Exception:
                pass

            logger.info("Config hot-reloaded successfully on-the-fly")
        except Exception as runtime_error:
            logger.error(
                "Failed to hot-reload configuration file", error=str(runtime_error)
            )
