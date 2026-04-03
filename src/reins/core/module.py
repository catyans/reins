"""Base class for all Reins modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reins.core.config import ReinsConfig
    from reins.core.events import EventBus
    from reins.core.models import SpanData
    from reins.core.storage import Storage


class ReinsModule(ABC):
    """Base class for Reins plugin modules (Budget, Trace, Lens, Pulse)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Module name: 'budget', 'trace', 'lens', 'pulse'."""
        ...

    @abstractmethod
    def init(self, event_bus: EventBus, storage: Storage, config: ReinsConfig) -> None:
        """Initialize the module. Register event listeners, create tables, etc."""
        ...

    def on_span_start(self, span: SpanData) -> SpanData:
        """Pre-hook before LLM call. Can modify span (e.g. Budget changes model).
        Must return the (possibly modified) span."""
        return span

    def on_span_end(self, span: SpanData) -> None:
        """Post-hook after LLM call. Record, evaluate, etc."""
        pass

    def shutdown(self) -> None:
        """Cleanup resources."""
        pass
