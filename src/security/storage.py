import asyncio
import json
import os
import time
from typing import Dict, Optional, Tuple

import aiosqlite
import structlog

from config.loader import ConfigManager
from config.schema import DatabaseDefConfig, WebhookDefConfig

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Storage Configuration
# ─────────────────────────────────────────────────────────────────────────────
DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "security.db")


class SecurityStorage:
    _instance = None
    _lock = asyncio.Lock()

    # In-Memory Caches (0ms latency access)
    _api_keys_cache: Dict[str, dict] = {}
    _bans_cache_ip: Dict[str, dict] = {}
    _bans_cache_key: Dict[str, dict] = {}
    _circuit_breakers_cache: Dict[str, dict] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SecurityStorage, cls).__new__(cls)
        return cls._instance

    # ─────────────────────────────────────────────────────────────────────────────
    # Initialization & State Sync
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def init_db(cls):
        """Initializes SQLite schema definitions and synchronizes the memory caches."""
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)

        async with cls._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await cls._create_schemas(db)
                await db.commit()

            await cls._reload_caches()
            logger.info(
                "Security database initialized and caches loaded.", path=DB_PATH
            )

    @classmethod
    async def _create_schemas(cls, db: aiosqlite.Connection):
        """Executes table creation if they don't exist yet."""
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                name TEXT PRIMARY KEY,
                secret_hash TEXT NOT NULL,
                mode TEXT NOT NULL,
                db_scope TEXT NOT NULL,
                fs_scope TEXT NOT NULL,
                rate_limit_override INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                type TEXT NOT NULL,
                identifier TEXT NOT NULL,
                reason TEXT NOT NULL,
                expires_at REAL,
                created_at REAL NOT NULL,
                PRIMARY KEY (type, identifier)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS circuit_breakers (
                key TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                failures INTEGER DEFAULT 0,
                successes INTEGER DEFAULT 0,
                last_failure_time REAL,
                tripped_at REAL
            )
        """)
        await db.execute("""
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
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS webhooks (
                name TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                rule TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1
            )
        """)

    @classmethod
    async def _reload_caches(cls):
        """Refreshes all memory caches directly from the SQLite truth source."""
        async with aiosqlite.connect(DB_PATH) as db:
            await cls._load_api_keys(db)
            await cls._load_bans(db)
            await cls._load_circuit_breakers(db)
            await cls._sync_dynamic_config(db)

    @classmethod
    async def _load_api_keys(cls, db: aiosqlite.Connection):
        cls._api_keys_cache.clear()
        query = "SELECT name, secret_hash, mode, db_scope, fs_scope, rate_limit_override FROM api_keys"
        async with db.execute(query) as cursor:
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
                    "rate_limit_override": row[5],
                }

    @classmethod
    async def _load_bans(cls, db: aiosqlite.Connection):
        cls._bans_cache_ip.clear()
        cls._bans_cache_key.clear()
        now = time.time()

        async with db.execute(
            "SELECT type, identifier, reason, expires_at FROM bans"
        ) as cursor:
            async for row in cursor:
                exp = row[3]
                if exp is not None and now > exp:
                    continue  # Lazily skip expired

                entry = {"reason": row[2], "expires_at": exp}
                if row[0] == "ip":
                    cls._bans_cache_ip[row[1]] = entry
                elif row[0] == "key":
                    cls._bans_cache_key[row[1]] = entry

    @classmethod
    async def _load_circuit_breakers(cls, db: aiosqlite.Connection):
        cls._circuit_breakers_cache.clear()
        query = "SELECT key, state, failures, successes, last_failure_time, tripped_at FROM circuit_breakers"
        async with db.execute(query) as cursor:
            async for row in cursor:
                cls._circuit_breakers_cache[row[0]] = {
                    "state": row[1],
                    "failures": row[2],
                    "successes": row[3],
                    "last_failure_time": row[4],
                    "tripped_at": row[5],
                }

    @classmethod
    async def _sync_dynamic_config(cls, db: aiosqlite.Connection):
        """Injects SQLite-defined databases and webhooks straight into the master config."""
        config = ConfigManager.get()

        db_query = "SELECT name, engine, url, mode, pool_min, pool_max, connection_timeout, idle_timeout, max_lifetime, dangerous_operations FROM databases"
        async with db.execute(db_query) as cursor:
            async for row in cursor:
                config.database[row[0]] = DatabaseDefConfig(
                    engine=row[1],
                    url=row[2],
                    mode=row[3],
                    pool_min=row[4],
                    pool_max=row[5],
                    connection_timeout=row[6],
                    idle_timeout=row[7],
                    max_lifetime=row[8],
                    dangerous_operations=bool(row[9]),
                )

        wh_query = "SELECT name, url, secret, rule, enabled FROM webhooks"
        async with db.execute(wh_query) as cursor:
            async for row in cursor:
                config.webhook[row[0]] = WebhookDefConfig(
                    url=row[1], secret=row[2], rule=row[3], enabled=bool(row[4])
                )

    # ─────────────────────────────────────────────────────────────────────────────
    # API Key Actions
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def add_api_key(
        cls,
        name: str,
        secret_hash: str,
        mode: str,
        db_scope: list,
        fs_scope: list,
        rate_limit: int,
    ):
        async with aiosqlite.connect(DB_PATH) as db:
            query = """
                INSERT OR REPLACE INTO api_keys
                (name, secret_hash, mode, db_scope, fs_scope, rate_limit_override, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                name,
                secret_hash,
                mode,
                json.dumps(db_scope),
                json.dumps(fs_scope),
                rate_limit,
                time.time(),
            )
            await db.execute(query, params)
            await db.commit()

        cls._api_keys_cache[name] = {
            "secret_hash": secret_hash,
            "mode": mode,
            "db_scope": db_scope,
            "fs_scope": fs_scope,
            "rate_limit_override": rate_limit,
        }

    @classmethod
    async def delete_api_key(cls, name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("DELETE FROM api_keys WHERE name = ?", (name,))
            await db.commit()
            if cursor.rowcount > 0:
                cls._api_keys_cache.pop(name, None)
                return True
        return False

    @classmethod
    async def update_api_key(cls, name: str, updates: dict) -> bool:
        """Mutable updates. The underlying secret_hash is preserved strictly."""
        existing = cls._api_keys_cache.get(name)
        if not existing:
            return False

        mode = updates.get("mode", existing["mode"])
        db_scope = updates.get("db_scope", existing["db_scope"])
        fs_scope = updates.get("fs_scope", existing["fs_scope"])
        rate_limit = updates.get("rate_limit_override", existing["rate_limit_override"])

        async with aiosqlite.connect(DB_PATH) as db:
            query = """
                UPDATE api_keys SET mode = ?, db_scope = ?, fs_scope = ?, rate_limit_override = ?
                WHERE name = ?
            """
            cursor = await db.execute(
                query,
                (mode, json.dumps(db_scope), json.dumps(fs_scope), rate_limit, name),
            )
            await db.commit()
            if cursor.rowcount == 0:
                return False

        existing.update(
            {
                "mode": mode,
                "db_scope": db_scope,
                "fs_scope": fs_scope,
                "rate_limit_override": rate_limit,
            }
        )
        return True

    @classmethod
    def get_api_key(cls, name: str) -> Optional[dict]:
        return cls._api_keys_cache.get(name)

    @classmethod
    def get_all_keys(cls) -> Dict[str, dict]:
        return cls._api_keys_cache

    # ─────────────────────────────────────────────────────────────────────────────
    # Ban Actions
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def ban_entity(
        cls,
        entity_type: str,
        identifier: str,
        reason: str,
        duration_seconds: Optional[int] = None,
    ):
        expires_at = time.time() + duration_seconds if duration_seconds else None

        async with aiosqlite.connect(DB_PATH) as db:
            query = "INSERT OR REPLACE INTO bans (type, identifier, reason, expires_at, created_at) VALUES (?, ?, ?, ?, ?)"
            await db.execute(
                query, (entity_type, identifier, reason, expires_at, time.time())
            )
            await db.commit()

        entry = {"reason": reason, "expires_at": expires_at}
        if entity_type == "ip":
            cls._bans_cache_ip[identifier] = entry
        elif entity_type == "key":
            cls._bans_cache_key[identifier] = entry

    @classmethod
    async def unban_entity(cls, entity_type: str, identifier: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "DELETE FROM bans WHERE type = ? AND identifier = ?",
                (entity_type, identifier),
            )
            await db.commit()

            if cursor.rowcount > 0:
                cache = (
                    cls._bans_cache_ip if entity_type == "ip" else cls._bans_cache_key
                )
                cache.pop(identifier, None)
                return True
        return False

    @classmethod
    def check_ban(cls, entity_type: str, identifier: str) -> Tuple[bool, Optional[str]]:
        """Fast-path for checking if an entity is currently banned."""
        cache = cls._bans_cache_ip if entity_type == "ip" else cls._bans_cache_key
        entry = cache.get(identifier)

        if not entry:
            return False, None

        if entry["expires_at"] is not None and time.time() > entry["expires_at"]:
            cache.pop(identifier, None)
            asyncio.create_task(
                cls.unban_entity(entity_type, identifier)
            )  # Lazy cleanup
            return False, None

        return True, entry["reason"]

    @classmethod
    def list_bans(cls) -> dict:
        now = time.time()
        active_ips = {
            k: v
            for k, v in cls._bans_cache_ip.items()
            if v["expires_at"] is None or v["expires_at"] > now
        }
        active_keys = {
            k: v
            for k, v in cls._bans_cache_key.items()
            if v["expires_at"] is None or v["expires_at"] > now
        }
        return {"ip_bans": active_ips, "key_bans": active_keys}

    # ─────────────────────────────────────────────────────────────────────────────
    # Circuit Breaker Actions
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def update_circuit(
        cls,
        key: str,
        state: str,
        failures: int,
        successes: int,
        last_failure_time: Optional[float],
        tripped_at: Optional[float],
    ):
        """Persist circuit breaker changes without blocking runtime tracking."""
        async with aiosqlite.connect(DB_PATH) as db:
            query = "INSERT OR REPLACE INTO circuit_breakers (key, state, failures, successes, last_failure_time, tripped_at) VALUES (?, ?, ?, ?, ?, ?)"
            await db.execute(
                query, (key, state, failures, successes, last_failure_time, tripped_at)
            )
            await db.commit()

        cls._circuit_breakers_cache[key] = {
            "state": state,
            "failures": failures,
            "successes": successes,
            "last_failure_time": last_failure_time,
            "tripped_at": tripped_at,
        }

    @classmethod
    def get_circuit_cache(cls, key: str) -> dict:
        if key not in cls._circuit_breakers_cache:
            cls._circuit_breakers_cache[key] = {
                "state": "closed",
                "failures": 0,
                "successes": 0,
                "last_failure_time": None,
                "tripped_at": None,
            }
        return cls._circuit_breakers_cache[key]

    @classmethod
    def get_all_circuits(cls) -> Dict[str, dict]:
        return cls._circuit_breakers_cache

    # ─────────────────────────────────────────────────────────────────────────────
    # Dynamic Configuration Subsystem
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def add_database(cls, name: str, cfg: dict):
        async with aiosqlite.connect(DB_PATH) as db:
            query = """
                INSERT OR REPLACE INTO databases
                (name, engine, url, mode, pool_min, pool_max, connection_timeout, idle_timeout, max_lifetime, dangerous_operations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                name,
                cfg["engine"],
                cfg["url"],
                cfg.get("mode", "readwrite"),
                cfg.get("pool_min", 2),
                cfg.get("pool_max", 20),
                cfg.get("connection_timeout", 5),
                cfg.get("idle_timeout", 300),
                cfg.get("max_lifetime", 1800),
                int(cfg.get("dangerous_operations", False)),
            )
            await db.execute(query, params)
            await db.commit()

        await cls._reload_caches()

    @classmethod
    async def delete_database(cls, name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("DELETE FROM databases WHERE name = ?", (name,))
            await db.commit()
            if cursor.rowcount > 0:
                config = ConfigManager.get()
                config.database.pop(name, None)
                return True
        return False

    @classmethod
    async def update_database(cls, name: str, updates: dict) -> bool:
        """Dynamically applies partial updates to a stored database definition."""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT * FROM databases WHERE name = ?", (name,)
            ) as cursor:
                if not await cursor.fetchone():
                    return False

            allowed_fields = [
                "engine",
                "url",
                "mode",
                "pool_min",
                "pool_max",
                "connection_timeout",
                "idle_timeout",
                "max_lifetime",
                "dangerous_operations",
            ]

            sets, vals = [], []
            for field in allowed_fields:
                if field in updates:
                    val = updates[field]
                    if field == "dangerous_operations":
                        val = int(val)
                    sets.append(f"{field} = ?")
                    vals.append(val)

            if not sets:
                return False

            vals.append(name)
            await db.execute(
                f"UPDATE databases SET {', '.join(sets)} WHERE name = ?", vals
            )
            await db.commit()

        await cls._reload_caches()
        return True

    @classmethod
    async def add_webhook(cls, name: str, cfg: dict):
        async with aiosqlite.connect(DB_PATH) as db:
            query = "INSERT OR REPLACE INTO webhooks (name, url, secret, rule, enabled) VALUES (?, ?, ?, ?, ?)"
            params = (
                name,
                cfg["url"],
                cfg["secret"],
                cfg["rule"],
                int(cfg.get("enabled", True)),
            )
            await db.execute(query, params)
            await db.commit()

        await cls._reload_caches()

    @classmethod
    async def delete_webhook(cls, name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("DELETE FROM webhooks WHERE name = ?", (name,))
            await db.commit()
            if cursor.rowcount > 0:
                config = ConfigManager.get()
                config.webhook.pop(name, None)
                return True
        return False

    @classmethod
    async def update_webhook(cls, name: str, updates: dict) -> bool:
        """Dynamically patches an active webhook."""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT * FROM webhooks WHERE name = ?", (name,)
            ) as cursor:
                if not await cursor.fetchone():
                    return False

            sets, vals = [], []
            for field in ["url", "rule", "enabled"]:
                if field in updates:
                    val = updates[field]
                    if field == "enabled":
                        val = int(val)
                    sets.append(f"{field} = ?")
                    vals.append(val)

            if not sets:
                return False

            vals.append(name)
            await db.execute(
                f"UPDATE webhooks SET {', '.join(sets)} WHERE name = ?", vals
            )
            await db.commit()

        await cls._reload_caches()
        return True
