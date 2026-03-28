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

logger = structlog.get_logger()

class CircuitState(str, Enum):
    CLOSED = "closed"   # Normal operation
    OPEN = "open"       # Failing — rejecting requests
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Per-resource circuit breaker tracking failure/success windows."""

    _circuits: Dict[str, dict] = {}

    @classmethod
    def _get_circuit(cls, key: str) -> dict:
        if key not in cls._circuits:
            cls._circuits[key] = {
                "state": CircuitState.CLOSED,
                "failures": 0,
                "successes": 0,
                "last_failure_time": None,
                "tripped_at": None,
            }
        return cls._circuits[key]

    @classmethod
    def is_open(cls, key: str) -> bool:
        config = ConfigManager.get()
        cb = config.circuit_breaker
        if not cb.enabled:
            return False

        circuit = cls._get_circuit(key)
        if circuit["state"] == CircuitState.OPEN:
            # Check if timeout has elapsed to attempt HALF_OPEN
            if circuit["tripped_at"] and (time.time() - circuit["tripped_at"]) >= cb.timeout:
                circuit["state"] = CircuitState.HALF_OPEN
                circuit["successes"] = 0
                logger.info("Circuit half-opened", key=key)
                return False
            return True
        return False

    @classmethod
    def record_success(cls, key: str):
        config = ConfigManager.get()
        cb = config.circuit_breaker
        if not cb.enabled:
            return

        circuit = cls._get_circuit(key)
        if circuit["state"] == CircuitState.HALF_OPEN:
            circuit["successes"] += 1
            if circuit["successes"] >= cb.success_threshold:
                circuit["state"] = CircuitState.CLOSED
                circuit["failures"] = 0
                logger.info("Circuit closed (recovered)", key=key)
        elif circuit["state"] == CircuitState.CLOSED:
            # Reset failure count on success
            circuit["failures"] = max(0, circuit["failures"] - 1)

    @classmethod
    def record_failure(cls, key: str):
        config = ConfigManager.get()
        cb = config.circuit_breaker
        if not cb.enabled:
            return

        circuit = cls._get_circuit(key)
        circuit["failures"] += 1
        circuit["last_failure_time"] = time.time()

        if circuit["failures"] >= cb.failure_threshold:
            circuit["state"] = CircuitState.OPEN
            circuit["tripped_at"] = time.time()
            logger.warning("Circuit tripped OPEN", key=key, failures=circuit["failures"])

    @classmethod
    def get_state(cls, key: str) -> dict:
        circuit = cls._get_circuit(key)
        return {
            "key": key,
            "state": circuit["state"].value,
            "failures": circuit["failures"],
            "last_failure_time": circuit["last_failure_time"],
        }

    @classmethod
    def all_states(cls) -> Dict[str, dict]:
        return {k: cls.get_state(k) for k in cls._circuits}
