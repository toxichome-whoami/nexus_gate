import time
import asyncio
import os
import aiosqlite
import structlog
from typing import Any, Optional
import orjson

from config.loader import ConfigManager

logger = structlog.get_logger()

DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "cache.db")

# ─────────────────────────────────────────────────────────────────────────────
# Internal Rate Limit Mutators
# ─────────────────────────────────────────────────────────────────────────────

async def _is_ip_penalized(db, penalty_key: str, now: float) -> bool:
    """Checks the SQLite payload determining if severe lockouts are active."""
    async with db.execute('SELECT expires_at FROM cache WHERE key = ?', (penalty_key,)) as cursor:
        row = await cursor.fetchone()
        if row and (row[0] is None or now < row[0]):
            return True
    return False

async def _enforce_rate_penalty(db, limits_key: str, penalty_key: str, now: float, window: int, penalty_cooldown: int) -> None:
    """Parses previous violations incrementing or hard-banning connection signatures."""
    violation_key = f"rl:violations:{limits_key}"
    v_count = 1
    
    async with db.execute('SELECT value FROM cache WHERE key = ?', (violation_key,)) as cursor:
        v_row = await cursor.fetchone()
        if v_row:
            try:
                v_count = orjson.loads(v_row[0]) + 1
            except Exception:
                pass
    
    # Store aggregated tracking footprint
    await db.execute(
        'INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)', 
        (violation_key, orjson.dumps(v_count), now + (window * 2))
    )
    
    # Commit absolute ban if tolerance exceeded
    if v_count >= 10:
        await db.execute(
            'INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)', 
            (penalty_key, orjson.dumps(True), now + penalty_cooldown)
        )

# ─────────────────────────────────────────────────────────────────────────────
# Core Adapter
# ─────────────────────────────────────────────────────────────────────────────

class SQLiteCache:
    """Local resilient disk caching backing preventing total memory exhaustion."""
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SQLiteCache, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def init_db(cls):
        os.makedirs(DB_DIR, exist_ok=True)
            
        async with cls._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value BLOB NOT NULL,
                        expires_at REAL
                    )
                ''')
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS rate_limits (
                        key TEXT NOT NULL,
                        timestamp REAL NOT NULL
                    )
                ''')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_rl_key_time ON rate_limits(key, timestamp)')
                await db.commit()
                logger.info("Initialized SQLite cache DB bindings", path=DB_PATH)

    @classmethod
    async def _cleanup_expired(cls, db):
        now = time.time()
        await db.execute('DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at < ?', (now,))

    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT value, expires_at FROM cache WHERE key = ?', (key,)) as cursor:
                row = await cursor.fetchone()
                
                if not row:
                    return None
                    
                val, exp = row
                if exp is not None and time.time() > exp:
                    asyncio.create_task(cls.delete(key))
                    return None
                    
                try:
                    return orjson.loads(val)
                except Exception:
                    return val

    @classmethod
    async def set(cls, key: str, value: Any, ttl: Optional[float] = None) -> None:
        config = ConfigManager.get()
        ttl_val = float(ttl) if ttl is not None else float(config.cache.default_ttl)
        expires_at = time.time() + ttl_val
        
        if not isinstance(value, (str, bytes)):
            value = orjson.dumps(value)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)', (key, value, expires_at))
            await cls._cleanup_expired(db)
            await db.commit()

    @classmethod
    async def delete(cls, key: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('DELETE FROM cache WHERE key = ?', (key,))
            await db.commit()
            return cursor.rowcount > 0

    @classmethod
    async def flush(cls) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('DELETE FROM cache')
            await db.execute('DELETE FROM rate_limits')
            await db.commit()

    @classmethod
    async def check_rate_limit(cls, limits_key: str, window: int, limit: int, penalty_key: str, burst: int, penalty_cooldown: int) -> tuple[bool, int]:
        """Atomically evaluates database window checks guaranteeing cross-worker synchronization."""
        now = time.time()
        window_start = now - window
        
        async with cls._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute('BEGIN EXCLUSIVE')
                try:
                    if await _is_ip_penalized(db, penalty_key, now):
                        await db.commit()
                        return True, limit + 1
                    
                    await db.execute('DELETE FROM rate_limits WHERE key = ? AND timestamp < ?', (limits_key, window_start))
                    
                    async with db.execute('SELECT COUNT(*) FROM rate_limits WHERE key = ?', (limits_key,)) as cursor:
                        count = (await cursor.fetchone())[0]

                    if count >= limit + burst:
                        await _enforce_rate_penalty(db, limits_key, penalty_key, now, window, penalty_cooldown)
                        await db.commit()
                        return True, count
                    
                    await db.execute('INSERT INTO rate_limits (key, timestamp) VALUES (?, ?)', (limits_key, now))
                    await db.commit()
                    
                    return False, count + 1
                    
                except Exception as execution_error:
                    await db.rollback()
                    logger.error("SQLite rate limit check failed critically", error=str(execution_error))
                    return False, 0
