"""Simple async-friendly circuit breaker implementation."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config import CircuitBreakerConfig


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    config: CircuitBreakerConfig
    _state: CircuitState = CircuitState.CLOSED
    _failure_count: int = 0
    _last_failure: Optional[float] = None

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    async def allow_request(self) -> bool:
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if self._last_failure is None:
                    return False
                if time.time() - self._last_failure >= self.config.recovery_time_seconds:
                    self._state = CircuitState.HALF_OPEN
                    return True
                return False

            # HALF_OPEN allows a single trial request
            return True

    async def record_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure = None

    async def record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure = time.time()
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
            else:
                self._state = CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count
