"""Lens module: debugging and diagnostics."""

from __future__ import annotations

from reins.core.config import ReinsConfig
from reins.core.events import EventBus, Events
from reins.core.models import SpanData
from reins.core.module import ReinsModule
from reins.core.storage import Storage


class LensModule(ReinsModule):
    """Agent debugging: replay, context health, breakpoints, root cause analysis."""

    @property
    def name(self) -> str:
        return "lens"

    def init(self, event_bus: EventBus, storage: Storage, config: ReinsConfig) -> None:
        self._event_bus = event_bus
        self._storage = storage

    def on_span_end(self, span: SpanData) -> None:
        # Compute context health score (simplified heuristic for MVP)
        if span.context_tokens is not None and span.context_tokens > 0:
            # Heuristic: health degrades as context fills up
            # Assume 200k token window
            max_tokens = 200_000
            utilization = span.context_tokens / max_tokens
            # U-shaped degradation: starts degrading significantly after 50% utilization
            if utilization < 0.5:
                span.context_health = 1.0 - (utilization * 0.2)
            else:
                span.context_health = max(0.1, 1.0 - (utilization * 1.5))

            if span.context_health < 0.5:
                self._event_bus.emit(
                    Events.CONTEXT_ROT,
                    agent=span.metadata.get("agent_name", "unknown"),
                    health_score=span.context_health,
                )
