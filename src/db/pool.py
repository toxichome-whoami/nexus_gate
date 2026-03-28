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
    _instance = None
    _engines: Dict[str, DatabaseEngine] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabasePoolManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def get_engine(cls, alias: str) -> Optional[DatabaseEngine]:
        if alias in cls._engines:
            return cls._engines[alias]

        # Lazy init
        config = ConfigManager.get()
        db_config = config.database.get(alias)
        if not db_config:
            return None

        logger.info("Initializing database pool", alias=alias, engine=db_config.engine)
        
        engine: DatabaseEngine
        if db_config.engine == DbEngineType.SQLITE:
            engine = SQLiteEngine(db_config)
        elif db_config.engine == DbEngineType.POSTGRES:
            engine = PostgresEngine(db_config)
        elif db_config.engine in (DbEngineType.MYSQL, DbEngineType.MARIADB):
            engine = MySQLEngine(db_config)
        elif db_config.engine == DbEngineType.MSSQL:
            engine = MSSQLEngine(db_config)
        else:
            raise NotImplementedError(f"Database engine '{db_config.engine}' is not supported.")
        
        await engine.connect()
        cls._engines[alias] = engine
        return engine

    @classmethod
    async def shutdown(cls):
        logger.info("Shutting down database pools")
        for alias, engine in cls._engines.items():
            logger.info("Closing pool", alias=alias)
            await engine.disconnect()
        cls._engines.clear()
