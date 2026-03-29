import os
import time
import json
import asyncio
import aiosqlite
import structlog
from typing import Dict, Any, Optional, List, Tuple
from config.loader import ConfigManager
from config.schema import DatabaseDefConfig, WebhookDefConfig
import json

logger = structlog.get_logger()

# Constants
DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "security.db")

class SecurityStorage:
    _instance = None
    _lock = asyncio.Lock()

    # In-Memory Cache (Ultra-Fast layer for auth/bans)
    _api_keys_cache: Dict[str, dict] = {}
    _bans_cache_ip: Dict[str, dict] = {}
    _bans_cache_key: Dict[str, dict] = {}
    _circuit_breakers_cache: Dict[str, dict] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SecurityStorage, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def init_db(cls):
        """Initialize SQLite database, tables, and load caches."""
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)

        async with cls._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                # API Keys table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS api_keys (
                        name TEXT PRIMARY KEY,
                        secret_hash TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        db_scope TEXT NOT NULL,
                        fs_scope TEXT NOT NULL,
                        rate_limit_override INTEGER DEFAULT 0,
                        created_at REAL NOT NULL
                    )
                ''')
                # Bans table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS bans (
                        type TEXT NOT NULL, -- 'ip' or 'key'
                        identifier TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        expires_at REAL,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (type, identifier)
                    )
                ''')
                # Circuit Breakers table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS circuit_breakers (
                        key TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        failures INTEGER DEFAULT 0,
                        successes INTEGER DEFAULT 0,
                        last_failure_time REAL,
                        tripped_at REAL
                    )
                ''')
                # Dynamic Databases table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS databases (
                        name TEXT PRIMARY KEY,
                        engine TEXT NOT NULL,
                        url TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        pool_min INTEGER DEFAULT 2,
                        pool_max INTEGER DEFAULT 20,
                        connection_timeout INTEGER DEFAULT 5,
                        idle_timeout INTEGER DEFAULT 300,
                        max_lifetime INTEGER DEFAULT 1800,
                        dangerous_operations BOOLEAN DEFAULT 0
                    )
                ''')
                # Dynamic Webhooks table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS webhooks (
                        name TEXT PRIMARY KEY,
                        url TEXT NOT NULL,
                        secret TEXT NOT NULL,
                        rule TEXT NOT NULL,
                        enabled BOOLEAN DEFAULT 1
                    )
                ''')
                await db.commit()

            # Load cache fully to memory for 0ms latency
            await cls._reload_caches()
            logger.info("Security database initialized and caches loaded.", path=DB_PATH)

    @classmethod
    async def _reload_caches(cls):
        """Load all entries into memory cache and inject configs."""
        async with aiosqlite.connect(DB_PATH) as db:
            # 1. API Keys
            cls._api_keys_cache.clear()
            async with db.execute('SELECT name, secret_hash, mode, db_scope, fs_scope, rate_limit_override FROM api_keys') as cursor:
                async for row in cursor:
                    try:
                        db_scope = json.loads(row[3])
                        fs_scope = json.loads(row[4])
                    except Exception:
                        db_scope, fs_scope = ["*"], ["*"]

                    cls._api_keys_cache[row[0]] = {
                        "secret_hash": row[1],
                        "mode": row[2],
                        "db_scope": db_scope,
                        "fs_scope": fs_scope,
                        "rate_limit_override": row[5]
                    }

            # 2. Bans
            cls._bans_cache_ip.clear()
            cls._bans_cache_key.clear()
            now = time.time()
            async with db.execute('SELECT type, identifier, reason, expires_at FROM bans') as cursor:
                async for row in cursor:
                    exp = row[3]
                    if exp is not None and now > exp:
                        continue # Expired, don't load. Will be lazily GC'd later.

                    entry = {"reason": row[2], "expires_at": exp}
                    if row[0] == 'ip':
                        cls._bans_cache_ip[row[1]] = entry
                    elif row[0] == 'key':
                        cls._bans_cache_key[row[1]] = entry

            # 3. Circuit Breakers
            cls._circuit_breakers_cache.clear()
            async with db.execute('SELECT key, state, failures, successes, last_failure_time, tripped_at FROM circuit_breakers') as cursor:
                async for row in cursor:
                    cls._circuit_breakers_cache[row[0]] = {
                        "state": row[1],
                        "failures": row[2],
                        "successes": row[3],
                        "last_failure_time": row[4],
                        "tripped_at": row[5]
                    }

            # 4. Synchronize Databases & Webhooks directly into ConfigManager memory
            config = ConfigManager.get()

            async with db.execute('SELECT name, engine, url, mode, pool_min, pool_max, connection_timeout, idle_timeout, max_lifetime, dangerous_operations FROM databases') as cursor:
                async for row in cursor:
                    config.database[row[0]] = DatabaseDefConfig(
                        engine=row[1], url=row[2], mode=row[3], pool_min=row[4], pool_max=row[5],
                        connection_timeout=row[6], idle_timeout=row[7], max_lifetime=row[8],
                        dangerous_operations=bool(row[9])
                    )

            async with db.execute('SELECT name, url, secret, rule, enabled FROM webhooks') as cursor:
                async for row in cursor:
                    config.webhook[row[0]] = WebhookDefConfig(
                        url=row[1], secret=row[2], rule=row[3], enabled=bool(row[4])
                    )

    # -- API KEY METHODS --
    @classmethod
    async def add_api_key(cls, name: str, secret_hash: str, mode: str, db_scope: list, fs_scope: list, rate_limit: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO api_keys
                (name, secret_hash, mode, db_scope, fs_scope, rate_limit_override, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, secret_hash, mode, json.dumps(db_scope), json.dumps(fs_scope), rate_limit, time.time()))
            await db.commit()

        cls._api_keys_cache[name] = {
            "secret_hash": secret_hash,
            "mode": mode,
            "db_scope": db_scope,
            "fs_scope": fs_scope,
            "rate_limit_override": rate_limit
        }

    @classmethod
    async def delete_api_key(cls, name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('DELETE FROM api_keys WHERE name = ?', (name,))
            await db.commit()
            if cursor.rowcount > 0:
                cls._api_keys_cache.pop(name, None)
                return True
        return False

    @classmethod
    def get_api_key(cls, name: str) -> Optional[dict]:
        return cls._api_keys_cache.get(name)

    @classmethod
    def get_all_keys(cls) -> Dict[str, dict]:
        return cls._api_keys_cache

    # -- BAN METHODS --
    @classmethod
    async def ban_entity(cls, entity_type: str, identifier: str, reason: str, duration_seconds: Optional[int] = None):
        expires_at = time.time() + duration_seconds if duration_seconds else None

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO bans
                (type, identifier, reason, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (entity_type, identifier, reason, expires_at, time.time()))
            await db.commit()

        entry = {"reason": reason, "expires_at": expires_at}
        if entity_type == 'ip':
            cls._bans_cache_ip[identifier] = entry
        elif entity_type == 'key':
            cls._bans_cache_key[identifier] = entry

    @classmethod
    async def unban_entity(cls, entity_type: str, identifier: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('DELETE FROM bans WHERE type = ? AND identifier = ?', (entity_type, identifier))
            await db.commit()

            if cursor.rowcount > 0:
                if entity_type == 'ip':
                    cls._bans_cache_ip.pop(identifier, None)
                elif entity_type == 'key':
                    cls._bans_cache_key.pop(identifier, None)
                return True
        return False

    @classmethod
    def check_ban(cls, entity_type: str, identifier: str) -> Tuple[bool, Optional[str]]:
        cache = cls._bans_cache_ip if entity_type == 'ip' else cls._bans_cache_key
        entry = cache.get(identifier)

        if not entry:
            return False, None

        if entry["expires_at"] is not None and time.time() > entry["expires_at"]:
            # Expired in cache, we lazily rely on background cleanup or just pop it
            cache.pop(identifier, None)
            asyncio.create_task(cls.unban_entity(entity_type, identifier)) # Async cleanup
            return False, None

        return True, entry["reason"]

    @classmethod
    def list_bans(cls) -> dict:
        # Note: Lazy sync ignores precise cleanup, safe enough for admin view
        now = time.time()
        active_ip = {k: v for k, v in cls._bans_cache_ip.items() if v["expires_at"] is None or v["expires_at"] > now}
        active_key = {k: v for k, v in cls._bans_cache_key.items() if v["expires_at"] is None or v["expires_at"] > now}
        return {"ip_bans": active_ip, "key_bans": active_key}

    # -- CIRCUIT BREAKER METHODS --
    @classmethod
    async def update_circuit(cls, key: str, state: str, failures: int, successes: int, last_failure_time: Optional[float], tripped_at: Optional[float]):
        """Persist circuit breaker changes. Fired asynchronously in background to not slow latency."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO circuit_breakers
                (key, state, failures, successes, last_failure_time, tripped_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (key, state, failures, successes, last_failure_time, tripped_at))
            await db.commit()

        cls._circuit_breakers_cache[key] = {
            "state": state, "failures": failures, "successes": successes,
            "last_failure_time": last_failure_time, "tripped_at": tripped_at
        }

    @classmethod
    def get_circuit_cache(cls, key: str) -> dict:
        if key not in cls._circuit_breakers_cache:
            cls._circuit_breakers_cache[key] = {
                "state": "closed", "failures": 0, "successes": 0,
                "last_failure_time": None, "tripped_at": None
            }
        return cls._circuit_breakers_cache[key]

    @classmethod
    def get_all_circuits(cls) -> Dict[str, dict]:
        return cls._circuit_breakers_cache

    # -- DYNAMIC CONFIGURATION (Databases / Webhooks) METHODS --
    @classmethod
    async def add_database(cls, name: str, cfg: dict):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO databases
                (name, engine, url, mode, pool_min, pool_max, connection_timeout, idle_timeout, max_lifetime, dangerous_operations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name, cfg["engine"], cfg["url"], cfg.get("mode", "readwrite"),
                cfg.get("pool_min", 2), cfg.get("pool_max", 20), cfg.get("connection_timeout", 5),
                cfg.get("idle_timeout", 300), cfg.get("max_lifetime", 1800), int(cfg.get("dangerous_operations", False))
            ))
            await db.commit()
        await cls._reload_caches() # Force config remap

    @classmethod
    async def delete_database(cls, name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('DELETE FROM databases WHERE name = ?', (name,))
            await db.commit()
            if cursor.rowcount > 0:
                config = ConfigManager.get()
                if name in config.database:
                    del config.database[name]
                return True
        return False

    @classmethod
    async def add_webhook(cls, name: str, cfg: dict):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO webhooks
                (name, url, secret, rule, enabled)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, cfg["url"], cfg["secret"], cfg["rule"], int(cfg.get("enabled", True))))
            await db.commit()
        await cls._reload_caches()

    @classmethod
    async def delete_webhook(cls, name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('DELETE FROM webhooks WHERE name = ?', (name,))
            await db.commit()
            if cursor.rowcount > 0:
                config = ConfigManager.get()
                if name in config.webhook:
                    del config.webhook[name]
                return True
        return False
