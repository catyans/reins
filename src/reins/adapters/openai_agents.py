"""OpenAI Agents SDK adapter.

Usage:
    from reins.adapters.openai_agents import ReinsTracingProcessor

    # Add alongside default OpenAI tracing
    from agents import add_trace_processor
    add_trace_processor(ReinsTracingProcessor())

    # Or replace default tracing entirely
    from agents import set_trace_processors
    set_trace_processors([ReinsTracingProcessor()])
"""

from __future__ import annotations

import logging
from typing import Any

from reins.adapters.base import finalize_span, process_span
from reins.core.models import SpanData, _utcnow

logger = logging.getLogger("reins.adapters.openai_agents")


class ReinsTracingProcessor:
    """Implements the OpenAI Agents SDK TracingProcessor protocol.

    The SDK calls:
    - on_trace_start(trace) / on_trace_end(trace)
    - on_span_start(span) / on_span_end(span)

    We map these to Reins SpanData and run through the module pipeline.
    """

    def __init__(self, agent_name: str = "openai_agents") -> None:
        self.agent_name = agent_name
        self._spans: dict[str, SpanData] = {}  # span_id -> SpanData
        self._trace_id: str | None = None

    def on_trace_start(self, trace: Any) -> None:
        """Called when a new agent trace begins."""
        self._trace_id = getattr(trace, "trace_id", None)

    def on_trace_end(self, trace: Any) -> None:
        """Called when an agent trace completes."""
        self._trace_id = None

    def on_span_start(self, span: Any) -> None:
        """Called when a span (LLM call, tool call, handoff, etc.) starts."""
        span_id = getattr(span, "span_id", str(id(span)))
        span_data = getattr(span, "span_data", None)

        # Determine span type from the SDK span
        span_type = "custom"
        provider = "openai"
        model = ""
        name = getattr(span, "name", "unknown")
        tool_name = None

        if span_data:
            # GenerationSpanData
            if hasattr(span_data, "model"):
                span_type = "llm"
                model = span_data.model or ""
                name = "openai.chat.create"
            # FunctionSpanData / ToolCallSpanData
            elif hasattr(span_data, "name") and hasattr(span_data, "input"):
                span_type = "tool"
                tool_name = span_data.name
                name = f"tool.{tool_name}"
            # HandoffSpanData
            elif hasattr(span_data, "from_agent"):
                span_type = "custom"
                name = (
                    f"handoff.{getattr(span_data, 'from_agent', '')}→"
                    f"{getattr(span_data, 'to_agent', '')}"
                )

        reins_span = SpanData(
            span_id=span_id,
            run_id=self._trace_id or "",
            span_type=span_type,
            name=name,
            provider=provider,
            model=model,
            model_requested=model,
            tool_name=tool_name,
        )
        reins_span.metadata["agent_name"] = self.agent_name

        # Pre-hooks
        try:
            reins_span = process_span(reins_span)
        except Exception:
            logger.debug("Budget check failed for OpenAI Agents span", exc_info=True)
            raise

        self._spans[span_id] = reins_span

    def on_span_end(self, span: Any) -> None:
        """Called when a span completes."""
        span_id = getattr(span, "span_id", str(id(span)))
        reins_span = self._spans.pop(span_id, None)
        if reins_span is None:
            return

        span_data = getattr(span, "span_data", None)

        if span_data and reins_span.span_type == "llm":
            # Extract usage from GenerationSpanData
            usage = getattr(span_data, "usage", None)
            if usage:
                tokens_in = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0)
                tokens_out = getattr(usage, "output_tokens", 0) or getattr(
                    usage, "completion_tokens", 0
                )
                reins_span.complete(tokens_in=tokens_in, tokens_out=tokens_out)
            else:
                reins_span.ended_at = _utcnow()
                reins_span.duration_ms = (
                    reins_span.ended_at - reins_span.started_at
                ).total_seconds() * 1000

            # Check model used (may differ from requested if degraded)
            model_used = getattr(span_data, "model", None)
            if model_used and model_used != reins_span.model_requested:
                reins_span.model = model_used
        elif span_data and reins_span.span_type == "tool":
            reins_span.ended_at = _utcnow()
            reins_span.duration_ms = (
                reins_span.ended_at - reins_span.started_at
            ).total_seconds() * 1000
            getattr(span_data, "output", None)
            reins_span.tool_status = "error" if getattr(span_data, "error", None) else "success"
            if getattr(span_data, "error", None):
                reins_span.tool_error = str(span_data.error)
        else:
            reins_span.ended_at = _utcnow()
            reins_span.duration_ms = (
                reins_span.ended_at - reins_span.started_at
            ).total_seconds() * 1000

        # Check for error
        error = getattr(span, "error", None) or getattr(span_data, "error", None)
        if error:
            reins_span.status = "error"
            reins_span.error_message = str(error)

        finalize_span(reins_span)

    def shutdown(self) -> None:
        """Cleanup."""
        self._spans.clear()

    def force_flush(self) -> None:
        """Flush pending spans."""
        for span_id in list(self._spans.keys()):
            span = self._spans.pop(span_id)
            span.ended_at = _utcnow()
            finalize_span(span)
