"""
Ban List: Persistent (SQLite-backed, cached) ban/unban registry
for IP addresses and API key names.
"""
import time
import structlog
from typing import Optional, Tuple

from security.storage import SecurityStorage

logger = structlog.get_logger()

class BanList:
    """High-performance cache-aside ban registry for IPs and API keys."""

    @classmethod
    async def ban_ip(cls, ip: str, reason: str, duration_seconds: Optional[int] = None):
        await SecurityStorage.ban_entity('ip', ip, reason, duration_seconds)
        logger.warning("IP banned", ip=ip, reason=reason, duration=duration_seconds)

    @classmethod
    async def unban_ip(cls, ip: str) -> bool:
        result = await SecurityStorage.unban_entity('ip', ip)
        if result:
            logger.info("IP unbanned", ip=ip)
        return result

    @classmethod
    def is_ip_banned(cls, ip: str) -> Tuple[bool, Optional[str]]:
        return SecurityStorage.check_ban('ip', ip)

    @classmethod
    async def ban_key(cls, key_name: str, reason: str, duration_seconds: Optional[int] = None):
        await SecurityStorage.ban_entity('key', key_name, reason, duration_seconds)
        logger.warning("API key banned", key_name=key_name, reason=reason)

    @classmethod
    async def unban_key(cls, key_name: str) -> bool:
        result = await SecurityStorage.unban_entity('key', key_name)
        if result:
            logger.info("API key unbanned", key_name=key_name)
        return result

    @classmethod
    def is_key_banned(cls, key_name: str) -> Tuple[bool, Optional[str]]:
        return SecurityStorage.check_ban('key', key_name)

    @classmethod
    def list_bans(cls) -> dict:
        return SecurityStorage.list_bans()
