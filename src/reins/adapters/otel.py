"""Generic OpenTelemetry SpanExporter for Reins.

This adapter auto-covers any OTel-native framework:
- Semantic Kernel (Microsoft)
- Pydantic AI
- Haystack (deepset)
- Mastra (TypeScript, via OTLP export)
- Any framework that emits OTel spans

Usage:
    from reins.adapters.otel import ReinsSpanExporter

    # With OpenTelemetry SDK
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ReinsSpanExporter()))

    # With Pydantic AI
    from pydantic_ai import Agent
    Agent.instrument(ReinsSpanExporter())

    # With Semantic Kernel (already OTel-native)
    # Just configure the TracerProvider with ReinsSpanExporter
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from reins.adapters.base import finalize_span
from reins.core.models import SpanData, _utcnow
from reins.core.pricing import get_price

logger = logging.getLogger("reins.adapters.otel")

# OpenTelemetry GenAI semantic convention attribute keys
_GENAI_SYSTEM = "gen_ai.system"
_GENAI_REQUEST_MODEL = "gen_ai.request.model"
_GENAI_RESPONSE_MODEL = "gen_ai.response.model"
_GENAI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
_GENAI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"


class ReinsSpanExporter:
    """OpenTelemetry SpanExporter that writes spans to Reins storage.

    Implements the OTel SpanExporter protocol:
    - export(spans) -> SpanExportResult
    - shutdown()
    - force_flush()
    """

    def __init__(self, agent_name: str = "otel") -> None:
        self.agent_name = agent_name

    def export(self, spans: Sequence[Any]) -> int:
        """Export OTel spans to Reins.

        Args:
            spans: Sequence of opentelemetry.sdk.trace.ReadableSpan

        Returns:
            SpanExportResult.SUCCESS (0) or FAILURE (1)
        """
        try:
            for otel_span in spans:
                reins_span = self._convert_span(otel_span)
                if reins_span:
                    finalize_span(reins_span)
            return 0  # SUCCESS
        except Exception:
            logger.debug("Failed to export OTel spans", exc_info=True)
            return 1  # FAILURE

    def shutdown(self) -> None:
        """Shutdown the exporter."""
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush pending exports."""
        return True

    def _convert_span(self, otel_span: Any) -> SpanData | None:
        """Convert an OTel ReadableSpan to Reins SpanData."""
        attrs = {}
        # Handle both dict-like and OTel attribute formats
        raw_attrs = getattr(otel_span, "attributes", {}) or {}
        if hasattr(raw_attrs, "items"):
            attrs = dict(raw_attrs.items())
        elif isinstance(raw_attrs, dict):
            attrs = raw_attrs

        # Determine span type from GenAI attributes
        provider = str(attrs.get(_GENAI_SYSTEM, ""))
        model = str(attrs.get(_GENAI_RESPONSE_MODEL, "") or attrs.get(_GENAI_REQUEST_MODEL, ""))
        tokens_in = int(attrs.get(_GENAI_INPUT_TOKENS, 0) or 0)
        tokens_out = int(attrs.get(_GENAI_OUTPUT_TOKENS, 0) or 0)

        is_llm = bool(provider or model or tokens_in or tokens_out)

        # Get span timing
        name = getattr(otel_span, "name", "unknown")
        span_id = ""
        if hasattr(otel_span, "context") and otel_span.context:
            ctx = otel_span.context
            span_id = format(getattr(ctx, "span_id", 0), "016x")

        trace_id = ""
        if hasattr(otel_span, "context") and otel_span.context:
            trace_id = format(getattr(ctx, "trace_id", 0), "032x")

        parent_span_id = None
        if hasattr(otel_span, "parent") and otel_span.parent:
            parent_span_id = format(getattr(otel_span.parent, "span_id", 0), "016x")

        # Create Reins span
        reins_span = SpanData(
            span_id=span_id or SpanData().span_id,
            run_id=trace_id[:36] if trace_id else "",
            parent_span_id=parent_span_id,
            span_type="llm" if is_llm else "custom",
            name=name,
            provider=provider,
            model=model,
            model_requested=str(attrs.get(_GENAI_REQUEST_MODEL, model)),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        reins_span.metadata["agent_name"] = self.agent_name

        # Calculate cost
        if tokens_in or tokens_out:
            reins_span.cost = get_price(provider, model, tokens_in, tokens_out)

        # Set timing
        start_time = getattr(otel_span, "start_time", None)
        end_time = getattr(otel_span, "end_time", None)
        if start_time and end_time:
            # OTel times are in nanoseconds
            reins_span.duration_ms = (end_time - start_time) / 1_000_000

        reins_span.ended_at = _utcnow()

        # Check status
        status = getattr(otel_span, "status", None)
        if status and hasattr(status, "status_code"):
            # StatusCode.ERROR = 2
            if status.status_code == 2:
                reins_span.status = "error"
                reins_span.error_message = getattr(status, "description", "")

        return reins_span


class ReinsSpanProcessor:
    """OpenTelemetry SpanProcessor that processes spans as they complete.

    Alternative to ReinsSpanExporter — processes inline rather than batched.

    Usage:
        from opentelemetry.sdk.trace import TracerProvider
        provider = TracerProvider()
        provider.add_span_processor(ReinsSpanProcessor())
    """

    def __init__(self, agent_name: str = "otel") -> None:
        self._exporter = ReinsSpanExporter(agent_name=agent_name)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        """Called when a span starts."""
        pass

    def on_end(self, span: Any) -> None:
        """Called when a span ends. Process immediately."""
        self._exporter.export([span])

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)
