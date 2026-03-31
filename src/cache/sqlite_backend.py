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

class SQLiteCache:
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SQLiteCache, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def init_db(cls):
        if not os.path.exists(DB_DIR):
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
                # Table specifically for sliding window rate limits
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS rate_limits (
                        key TEXT NOT NULL,
                        timestamp REAL NOT NULL
                    )
                ''')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_rl_key_time ON rate_limits(key, timestamp)')
                await db.commit()
                logger.info("Initialized SQLite cache DB", path=DB_PATH)

    @classmethod
    async def _cleanup_expired(cls, db):
        now = time.time()
        await db.execute('DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at < ?', (now,))

    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT value, expires_at FROM cache WHERE key = ?', (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    val, exp = row
                    if exp is not None and time.time() > exp:
                        asyncio.create_task(cls.delete(key))
                        return None
                    try:
                        return orjson.loads(val)
                    except Exception:
                        return val
        return None

    @classmethod
    async def set(cls, key: str, value: Any, ttl: Optional[float] = None) -> None:
        config = ConfigManager.get()
        ttl = float(ttl) if ttl is not None else float(config.cache.default_ttl)
        expires_at = time.time() + ttl
        
        if not isinstance(value, (str, bytes)):
            value = orjson.dumps(value)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO cache (key, value, expires_at)
                VALUES (?, ?, ?)
            ''', (key, value, expires_at))
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
        """Atomic sliding window rate limit check. Returns (is_violated, current_count)."""
        now = time.time()
        window_start = now - window
        
        # We need isolation level EXCLUSIVE to prevent race conditions during the check & insert
        async with cls._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute('BEGIN EXCLUSIVE')
                try:
                    # Check penalty
                    async with db.execute('SELECT expires_at FROM cache WHERE key = ?', (penalty_key,)) as cursor:
                        row = await cursor.fetchone()
                        if row and (row[0] is None or now < row[0]):
                            await db.commit()
                            return True, limit + 1
                    
                    # Clean up old timestamps
                    await db.execute('DELETE FROM rate_limits WHERE key = ? AND timestamp < ?', (limits_key, window_start))
                    
                    # Count remaining
                    async with db.execute('SELECT COUNT(*) FROM rate_limits WHERE key = ?', (limits_key,)) as cursor:
                        count = (await cursor.fetchone())[0]

                    if count >= limit + burst:
                        # Violation!
                        violation_key = f"rl:violations:{limits_key}"
                        v_count = 1
                        async with db.execute('SELECT value FROM cache WHERE key = ?', (violation_key,)) as cursor:
                            v_row = await cursor.fetchone()
                            if v_row:
                                try:
                                    v_count = orjson.loads(v_row[0]) + 1
                                except Exception:
                                    pass
                        
                        # Save violation count
                        await db.execute('INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)', 
                                         (violation_key, orjson.dumps(v_count), now + (window * 2)))
                        
                        if v_count >= 10:
                            # Apply penalty
                            await db.execute('INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)', 
                                             (penalty_key, orjson.dumps(True), now + penalty_cooldown))
                            
                        await db.commit()
                        return True, count
                    
                    # Safe to proceed, log timestamp
                    await db.execute('INSERT INTO rate_limits (key, timestamp) VALUES (?, ?)', (limits_key, now))
                    await db.commit()
                    return False, count + 1
                except Exception as e:
                    await db.rollback()
                    logger.error("SQLite rate limit check failed", error=str(e))
                    return False, 0
