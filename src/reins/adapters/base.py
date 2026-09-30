"""Base utilities shared by all framework adapters."""

from __future__ import annotations

import logging

from reins.core.context import get_current_run
from reins.core.decorators import _get_runtime
from reins.core.models import SpanData

logger = logging.getLogger("reins.adapters")


def _get_modules() -> list:
    """Get loaded modules from the runtime."""
    return _get_runtime().modules


def _get_storage():
    """Get storage from the runtime."""
    return _get_runtime().storage


def _get_event_bus():
    """Get event bus from the runtime."""
    return _get_runtime().event_bus


def process_span(span: SpanData) -> SpanData:
    """Run span through all module pre-hooks and return the (possibly modified) span."""
    for module in _get_modules():
        try:
            span = module.on_span_start(span)
        except Exception:
            logger.debug("Module on_span_start error", exc_info=True)
            raise
    return span


def finalize_span(span: SpanData) -> None:
    """Run span through all module post-hooks and persist."""
    run = get_current_run()

    for module in _get_modules():
        try:
            module.on_span_end(span)
        except Exception:
            logger.debug("Module on_span_end error", exc_info=True)

    _get_event_bus().emit("core.span_end", span=span)

    if run:
        run.add_span_cost(span)

    try:
        _get_storage().insert_span(span)
    except Exception:
        logger.debug("Failed to persist span", exc_info=True)
