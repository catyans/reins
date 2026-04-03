"""Pulse module: reliability and evaluation."""

from __future__ import annotations

from reins.core.config import ReinsConfig
from reins.core.events import EventBus, Events
from reins.core.models import SpanData
from reins.core.module import ReinsModule
from reins.core.storage import Storage


class PulseModule(ReinsModule):
    """Runtime evaluation, guardrails, reliability metrics, regression testing."""

    @property
    def name(self) -> str:
        return "pulse"

    def init(self, event_bus: EventBus, storage: Storage, config: ReinsConfig) -> None:
        self._event_bus = event_bus
        self._storage = storage

        # Listen for context rot events
        event_bus.on(Events.CONTEXT_ROT, self._on_context_rot)

    def on_span_end(self, span: SpanData) -> None:
        # Basic guardrail checks (expanded in Phase 3)
        if span.span_type == "llm" and span.status == "ok":
            flags = self._check_basic_guardrails(span)
            if flags:
                span.safety_flags = flags
                self._event_bus.emit(Events.GUARDRAIL_HIT, agent=span.metadata.get("agent_name"), flags=flags)

    def _check_basic_guardrails(self, span: SpanData) -> list[str] | None:
        # Placeholder: actual guardrail implementation in Phase 3
        return None

    def _on_context_rot(self, **kwargs) -> None:
        agent = kwargs.get("agent", "unknown")
        health = kwargs.get("health_score", 0)
        self._storage.insert_budget_event(
            agent_name=agent,
            event_type="context_rot",
            amount=0,
            balance_after=0,
            details=f"health_score={health}",
        )
