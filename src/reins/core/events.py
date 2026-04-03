"""Event bus for inter-module communication."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("reins.events")


class EventBus:
    """In-process event bus. The only communication channel between modules."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def on(self, event_name: str, callback: Callable[..., Any]) -> None:
        """Register an event listener."""
        self._listeners[event_name].append(callback)

    def off(self, event_name: str, callback: Callable[..., Any]) -> None:
        """Unregister an event listener."""
        listeners = self._listeners.get(event_name, [])
        if callback in listeners:
            listeners.remove(callback)

    def emit(self, event_name: str, **kwargs: Any) -> None:
        """Emit an event. All listeners are called synchronously (fail-safe)."""
        for callback in self._listeners.get(event_name, []):
            try:
                result = callback(**kwargs)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        pass  # No running loop, skip async callback
            except Exception:
                logger.debug("Event listener error for %s", event_name, exc_info=True)

    def clear(self) -> None:
        """Remove all listeners."""
        self._listeners.clear()


class Events:
    """Predefined event names."""

    # Core
    SPAN_START = "core.span_start"
    SPAN_END = "core.span_end"
    RUN_START = "core.run_start"
    RUN_END = "core.run_end"

    # Budget
    BUDGET_THRESHOLD = "budget.threshold_reached"
    BUDGET_EXCEEDED = "budget.exceeded"
    BUDGET_DEGRADED = "budget.degraded"
    BUDGET_CIRCUIT_BREAK = "budget.circuit_break"

    # Lens
    CONTEXT_ROT = "lens.context_rot_detected"
    HEALTH_DROP = "lens.health_score_drop"

    # Pulse
    QUALITY_DROP = "pulse.quality_drop"
    GUARDRAIL_HIT = "pulse.guardrail_hit"
