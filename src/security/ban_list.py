"""
Ban List: In-memory ban/unban registry for IP addresses and API key names.
"""

import time
from typing import Dict, Optional, Tuple

import structlog

logger = structlog.get_logger()


class BanList:
    """High-performance in-memory ban registry for IPs and API keys."""

    _bans_cache_ip: Dict[str, dict] = {}
    _bans_cache_key: Dict[str, dict] = {}

    # ─────────────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def _ban_entity(
        cls,
        entity_type: str,
        identifier: str,
        reason: str,
        duration_seconds: Optional[int] = None,
    ):
        expires_at = time.time() + duration_seconds if duration_seconds else None
        entry = {"reason": reason, "expires_at": expires_at}

        if entity_type == "ip":
            cls._bans_cache_ip[identifier] = entry
        elif entity_type == "key":
            cls._bans_cache_key[identifier] = entry

    @classmethod
    def _unban_entity(cls, entity_type: str, identifier: str) -> bool:
        cache = cls._bans_cache_ip if entity_type == "ip" else cls._bans_cache_key
        if identifier in cache:
            cache.pop(identifier)
            return True
        return False

    @classmethod
    def _check_ban(
        cls, entity_type: str, identifier: str
    ) -> Tuple[bool, Optional[str]]:
        cache = cls._bans_cache_ip if entity_type == "ip" else cls._bans_cache_key
        entry = cache.get(identifier)

        if not entry:
            return False, None

        if entry["expires_at"] is not None and time.time() > entry["expires_at"]:
            cache.pop(identifier, None)
            return False, None

        return True, entry["reason"]

    # ─────────────────────────────────────────────────────────────────────────────
    # IP Methods
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def ban_ip(cls, ip: str, reason: str, duration_seconds: Optional[int] = None):
        """Places a strict network-level lockout on an IP address."""
        cls._ban_entity("ip", ip, reason, duration_seconds)
        logger.warning("IP banned", ip=ip, reason=reason, duration=duration_seconds)

    @classmethod
    async def unban_ip(cls, ip: str) -> bool:
        """Removes an IP network-level lockout."""
        result = cls._unban_entity("ip", ip)
        if result:
            logger.info("IP unbanned", ip=ip)
        return result

    @classmethod
    def is_ip_banned(cls, ip: str) -> Tuple[bool, Optional[str]]:
        """Fast synchronous check verifying if the IP is actively locked out."""
        return cls._check_ban("ip", ip)

    # ─────────────────────────────────────────────────────────────────────────────
    # API Key Methods
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    async def ban_key(
        cls, key_name: str, reason: str, duration_seconds: Optional[int] = None
    ):
        """Immediately destroys session capability of an API Key."""
        cls._ban_entity("key", key_name, reason, duration_seconds)
        logger.warning("API key banned", key_name=key_name, reason=reason)

    @classmethod
    async def unban_key(cls, key_name: str) -> bool:
        """Restores session capability for a previously banned API Key."""
        result = cls._unban_entity("key", key_name)
        if result:
            logger.info("API key unbanned", key_name=key_name)
        return result

    @classmethod
    def is_key_banned(cls, key_name: str) -> Tuple[bool, Optional[str]]:
        """Fast synchronous check verifying identity access lockouts."""
        return cls._check_ban("key", key_name)

    # ─────────────────────────────────────────────────────────────────────────────
    # Introspection
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def list_bans(cls) -> dict:
        """Returns all currently active unexpired bans tracked dynamically."""
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
