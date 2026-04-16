import structlog
from typing import Dict, Optional

from config.loader import ConfigManager
from utils.types import DbEngineType
from db.engines.base import DatabaseEngine
from db.engines.sqlite import SQLiteEngine
from db.engines.postgres import PostgresEngine
from db.engines.mysql import MySQLEngine
from db.engines.mssql import MSSQLEngine

logger = structlog.get_logger()

class DatabasePoolManager:
    """Manages connection pools for multiple configured database engines."""
    
    _instance = None
    _engines: Dict[str, DatabaseEngine] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabasePoolManager, cls).__new__(cls)
        return cls._instance

    # ─────────────────────────────────────────────────────────────────────────────
    # Internal Engine Factory
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def _instantiate_engine(cls, db_config) -> DatabaseEngine:
        """Factory method mapping a config dialect enum to its engine instance."""
        engine_type = db_config.engine
        if engine_type == DbEngineType.SQLITE:
            return SQLiteEngine(db_config)
        
        if engine_type == DbEngineType.POSTGRES:
            return PostgresEngine(db_config)
            
        if engine_type in (DbEngineType.MYSQL, DbEngineType.MARIADB):
            return MySQLEngine(db_config)
            
        if engine_type == DbEngineType.MSSQL:
            return MSSQLEngine(db_config)
            
        raise NotImplementedError(f"Database engine '{engine_type}' is not supported.")

    # ─────────────────────────────────────────────────────────────────────────────
    # Public Lifecycle Hooks
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def get_engine(cls, alias: str) -> Optional[DatabaseEngine]:
        """Lazy loads and returns an active pooled engine connection."""
        if alias in cls._engines:
            return cls._engines[alias]

        # Lazy init configuration lookup
        config = ConfigManager.get()
        db_config = config.database.get(alias)
        if not db_config:
            return None

        logger.info("Initializing database pool", alias=alias, engine=db_config.engine)
        
        engine = cls._instantiate_engine(db_config)
        await engine.connect()
        
        cls._engines[alias] = engine
        return engine

    @classmethod
    async def remove_engine(cls, alias: str):
        """Dynamically unmounts and closes an active database pool."""
        if alias in cls._engines:
            engine = cls._engines.pop(alias)
            logger.info("Closing pool for dynamically removed database", alias=alias)
            await engine.disconnect()

    @classmethod
    async def shutdown(cls):
        """Graceful shutdown hook for all tracked pools."""
        logger.info("Shutting down database pools")
        for alias, engine in cls._engines.items():
            logger.info("Closing pool", alias=alias)
            try:
                await engine.disconnect()
            except (RuntimeError, Exception) as e:
                # Disconnects throw benign exceptions cleanly closing on existing SIGINT traps
                logger.debug("Pool close warning (safe to ignore)", alias=alias, error=str(e))
                
        cls._engines.clear()
        logger.info("Shutdown complete")
