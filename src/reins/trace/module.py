"""Trace module: deep observability for agent runs."""

from __future__ import annotations

from reins.core.config import ReinsConfig
from reins.core.events import EventBus
from reins.core.models import SpanData
from reins.core.module import ReinsModule
from reins.core.storage import Storage


class TraceModule(ReinsModule):
    """Deep observability: trace visualization, OTel export, cross-agent correlation."""

    @property
    def name(self) -> str:
        return "trace"

    def init(self, event_bus: EventBus, storage: Storage, config: ReinsConfig) -> None:
        self._storage = storage
        self._config = config

    def on_span_end(self, span: SpanData) -> None:
        # Trace module enriches spans with additional context info
        # (full implementation in Phase 2)
        pass
