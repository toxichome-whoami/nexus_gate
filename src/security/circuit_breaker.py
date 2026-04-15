"""
Circuit Breaker: Prevents cascade failures by tracking failures
per DB alias and tripping an OPEN state after threshold is exceeded.
"""
import asyncio
import time
import structlog
from typing import Dict
from enum import Enum

from config.loader import ConfigManager
from security.storage import SecurityStorage

logger = structlog.get_logger()

class CircuitState(str, Enum):
    CLOSED = "closed"   # Normal operation
    OPEN = "open"       # Failing — rejecting requests
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Per-resource circuit breaker tracking failure/success windows."""

    # ─────────────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def _get_circuit(cls, key: str) -> dict:
        return SecurityStorage.get_circuit_cache(key)

    @classmethod
    def _is_enabled(cls) -> bool:
        return ConfigManager.get().circuit_breaker.enabled

    @classmethod
    def _persist_state(cls, key: str, circuit: dict):
        """Asynchronously triggers the storage layer to commit state without blocking."""
        asyncio.create_task(SecurityStorage.update_circuit(
            key, circuit["state"], circuit["failures"], 
            circuit["successes"], circuit["last_failure_time"], circuit["tripped_at"]
        ))

    # ─────────────────────────────────────────────────────────────────────────────
    # Core Logic
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def is_open(cls, key: str) -> bool:
        """Determines if the circuit is currently rejecting traffic."""
        if not cls._is_enabled():
            return False

        config = ConfigManager.get().circuit_breaker
        circuit = cls._get_circuit(key)

        # Fast path
        if circuit["state"] != CircuitState.OPEN.value:
            return False

        # If Open, check if timeout elapsed to attempt Half-Open
        tripped_at = circuit["tripped_at"]
        if tripped_at and (time.time() - tripped_at) >= config.timeout:
            circuit["state"] = CircuitState.HALF_OPEN.value
            circuit["successes"] = 0
            logger.info("Circuit half-opened", key=key)
            cls._persist_state(key, circuit)
            return False

        return True

    @classmethod
    def record_success(cls, key: str):
        """Acknowledges a successful request, contributing to healing."""
        if not cls._is_enabled():
            return

        config = ConfigManager.get().circuit_breaker
        circuit = cls._get_circuit(key)
        state_changed = False
        
        # Heal from HALF_OPEN
        if circuit["state"] == CircuitState.HALF_OPEN.value:
            circuit["successes"] += 1
            if circuit["successes"] >= config.success_threshold:
                circuit["state"] = CircuitState.CLOSED.value
                circuit["failures"] = 0
                state_changed = True
                logger.info("Circuit closed (recovered)", key=key)
                
        # Natural decay of failure count
        elif circuit["state"] == CircuitState.CLOSED.value:
            if circuit["failures"] > 0:
                circuit["failures"] = max(0, circuit["failures"] - 1)
                # Note: Deliberately avoiding state persistence on natural decay to save disk I/O

        if state_changed:
            cls._persist_state(key, circuit)

    @classmethod
    def record_failure(cls, key: str):
        """Acknowledges a failed request, moving closer to an OPEN trip."""
        if not cls._is_enabled():
            return

        config = ConfigManager.get().circuit_breaker
        circuit = cls._get_circuit(key)
        circuit["failures"] += 1
        circuit["last_failure_time"] = time.time()

        # Check Trip condition
        should_trip = circuit["failures"] >= config.failure_threshold
        is_closed = circuit["state"] != CircuitState.OPEN.value

        if should_trip and is_closed:
            circuit["state"] = CircuitState.OPEN.value
            circuit["tripped_at"] = time.time()
            logger.warning("Circuit tripped OPEN", key=key, failures=circuit["failures"])
            cls._persist_state(key, circuit)

    # ─────────────────────────────────────────────────────────────────────────────
    # Introspection
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def get_state(cls, key: str) -> dict:
        circuit = cls._get_circuit(key)
        return {
            "key": key,
            "state": circuit["state"],
            "failures": circuit["failures"],
            "last_failure_time": circuit["last_failure_time"],
        }

    @classmethod
    def all_states(cls) -> Dict[str, dict]:
        return {k: cls.get_state(k) for k in SecurityStorage.get_all_circuits()}

    @classmethod
    async def reset(cls, key: str):
        """Manually forces a closed state overlay."""
        await SecurityStorage.update_circuit(key, CircuitState.CLOSED.value, 0, 0, None, None)
