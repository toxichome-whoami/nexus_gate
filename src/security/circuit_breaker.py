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

    @classmethod
    def _get_circuit(cls, key: str) -> dict:
        return SecurityStorage.get_circuit_cache(key)

    @classmethod
    def is_open(cls, key: str) -> bool:
        config = ConfigManager.get()
        cb = config.circuit_breaker
        if not cb.enabled:
            return False

        circuit = cls._get_circuit(key)
        if circuit["state"] == CircuitState.OPEN.value:
            # Check if timeout has elapsed to attempt HALF_OPEN
            if circuit["tripped_at"] and (time.time() - circuit["tripped_at"]) >= cb.timeout:
                circuit["state"] = CircuitState.HALF_OPEN.value
                circuit["successes"] = 0
                logger.info("Circuit half-opened", key=key)
                
                # Persist state change
                asyncio.create_task(SecurityStorage.update_circuit(
                    key, circuit["state"], circuit["failures"], 
                    circuit["successes"], circuit["last_failure_time"], circuit["tripped_at"]
                ))
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
        state_changed = False
        
        if circuit["state"] == CircuitState.HALF_OPEN.value:
            circuit["successes"] += 1
            if circuit["successes"] >= cb.success_threshold:
                circuit["state"] = CircuitState.CLOSED.value
                circuit["failures"] = 0
                state_changed = True
                logger.info("Circuit closed (recovered)", key=key)
        elif circuit["state"] == CircuitState.CLOSED.value:
            # Reset failure count on success
            if circuit["failures"] > 0:
                circuit["failures"] = max(0, circuit["failures"] - 1)
                # We don't persist failure decr to avoid disk IO on every success
                
        if state_changed:
            asyncio.create_task(SecurityStorage.update_circuit(
                key, circuit["state"], circuit["failures"], 
                circuit["successes"], circuit["last_failure_time"], circuit["tripped_at"]
            ))

    @classmethod
    def record_failure(cls, key: str):
        config = ConfigManager.get()
        cb = config.circuit_breaker
        if not cb.enabled:
            return

        circuit = cls._get_circuit(key)
        circuit["failures"] += 1
        circuit["last_failure_time"] = time.time()
        
        state_changed = False

        if circuit["failures"] >= cb.failure_threshold and circuit["state"] != CircuitState.OPEN.value:
            circuit["state"] = CircuitState.OPEN.value
            circuit["tripped_at"] = time.time()
            state_changed = True
            logger.warning("Circuit tripped OPEN", key=key, failures=circuit["failures"])

        if state_changed:
            asyncio.create_task(SecurityStorage.update_circuit(
                key, circuit["state"], circuit["failures"], 
                circuit["successes"], circuit["last_failure_time"], circuit["tripped_at"]
            ))

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
        circuits = SecurityStorage.get_all_circuits()
        return {k: cls.get_state(k) for k in circuits}

    @classmethod
    async def reset(cls, key: str):
        await SecurityStorage.update_circuit(key, CircuitState.CLOSED.value, 0, 0, None, None)
