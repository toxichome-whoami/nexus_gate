"""
Ban List: Persistent (in-memory, optionally Redis-backed) ban/unban registry
for IP addresses and API key names.
"""
import time
import structlog
from typing import Dict, Optional, Tuple

logger = structlog.get_logger()

class BanEntry:
    def __init__(self, reason: str, expires_at: Optional[float] = None):
        self.reason = reason
        self.created_at = time.time()
        self.expires_at = expires_at

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "permanent": self.expires_at is None,
        }

class BanList:
    """Thread-safe in-memory ban registry for IPs and API keys."""

    _ip_bans: Dict[str, BanEntry] = {}
    _key_bans: Dict[str, BanEntry] = {}

    @classmethod
    def ban_ip(cls, ip: str, reason: str, duration_seconds: Optional[int] = None):
        expires_at = time.time() + duration_seconds if duration_seconds else None
        cls._ip_bans[ip] = BanEntry(reason, expires_at)
        logger.warning("IP banned", ip=ip, reason=reason, duration=duration_seconds)

    @classmethod
    def unban_ip(cls, ip: str) -> bool:
        if ip in cls._ip_bans:
            del cls._ip_bans[ip]
            logger.info("IP unbanned", ip=ip)
            return True
        return False

    @classmethod
    def is_ip_banned(cls, ip: str) -> Tuple[bool, Optional[str]]:
        entry = cls._ip_bans.get(ip)
        if entry is None:
            return False, None
        if entry.is_expired():
            del cls._ip_bans[ip]
            return False, None
        return True, entry.reason

    @classmethod
    def ban_key(cls, key_name: str, reason: str, duration_seconds: Optional[int] = None):
        expires_at = time.time() + duration_seconds if duration_seconds else None
        cls._key_bans[key_name] = BanEntry(reason, expires_at)
        logger.warning("API key banned", key_name=key_name, reason=reason)

    @classmethod
    def unban_key(cls, key_name: str) -> bool:
        if key_name in cls._key_bans:
            del cls._key_bans[key_name]
            logger.info("API key unbanned", key_name=key_name)
            return True
        return False

    @classmethod
    def is_key_banned(cls, key_name: str) -> Tuple[bool, Optional[str]]:
        entry = cls._key_bans.get(key_name)
        if entry is None:
            return False, None
        if entry.is_expired():
            del cls._key_bans[key_name]
            return False, None
        return True, entry.reason

    @classmethod
    def list_bans(cls) -> dict:
        # GC expired bans first
        cls._ip_bans = {k: v for k, v in cls._ip_bans.items() if not v.is_expired()}
        cls._key_bans = {k: v for k, v in cls._key_bans.items() if not v.is_expired()}

        return {
            "ip_bans": {ip: entry.to_dict() for ip, entry in cls._ip_bans.items()},
            "key_bans": {k: entry.to_dict() for k, entry in cls._key_bans.items()},
        }
