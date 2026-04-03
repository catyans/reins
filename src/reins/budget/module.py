"""Budget module: cost governance for AI agents."""

from __future__ import annotations

from reins.budget.engine import BudgetEngine
from reins.core.config import ReinsConfig
from reins.core.events import EventBus
from reins.core.models import SpanData
from reins.core.module import ReinsModule
from reins.core.storage import Storage


class BudgetModule(ReinsModule):
    """Runtime cost governance: budget enforcement, degradation, circuit breaker."""

    @property
    def name(self) -> str:
        return "budget"

    def init(self, event_bus: EventBus, storage: Storage, config: ReinsConfig) -> None:
        self._engine = BudgetEngine(event_bus, storage, config)

    def on_span_start(self, span: SpanData) -> SpanData:
        return self._engine.on_span_start(span)

    def on_span_end(self, span: SpanData) -> None:
        self._engine.on_span_end(span)

    def shutdown(self) -> None:
        pass
