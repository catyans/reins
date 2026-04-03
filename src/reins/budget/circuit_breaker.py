"""Circuit breaker: detect and halt runaway agent loops."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone


class CircuitBreaker:
    """Detects agent loops by tracking call frequency."""

    def __init__(
        self,
        max_calls_per_minute: int = 30,
        window: timedelta = timedelta(minutes=1),
    ):
        self._max = max_calls_per_minute
        self._window = window
        self._history: dict[str, deque[datetime]] = {}
        self._tripped: set[str] = set()

    def check(self, agent_name: str) -> bool:
        """Returns True if the circuit should break (too many calls)."""
        now = datetime.now(timezone.utc)
        history = self._history.setdefault(agent_name, deque())

        # Purge expired entries
        while history and (now - history[0]) > self._window:
            history.popleft()

        history.append(now)

        if len(history) > self._max:
            self._tripped.add(agent_name)
            return True

        return False

    def reset(self, agent_name: str) -> None:
        """Reset the circuit breaker for an agent."""
        self._history.pop(agent_name, None)
        self._tripped.discard(agent_name)

    def is_tripped(self, agent_name: str) -> bool:
        return agent_name in self._tripped
