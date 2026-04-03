"""Core data models for Reins."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class SpanData:
    """A single LLM call, tool call, or custom operation."""

    span_id: str = field(default_factory=_uuid)
    run_id: str = ""
    parent_span_id: str | None = None
    span_type: str = "llm"  # llm | tool | retrieval | custom
    name: str = ""
    provider: str = ""  # anthropic | openai | google
    model: str = ""
    model_requested: str = ""  # original model before degradation
    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    duration_ms: float = 0.0

    # LLM fields
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Decimal = field(default_factory=lambda: Decimal("0"))
    degraded: bool = False

    # Tool fields
    tool_name: str | None = None
    tool_status: str | None = None  # success | error | timeout
    tool_error: str | None = None

    # Context health (filled by Lens module)
    context_tokens: int | None = None
    context_health: float | None = None

    # Eval scores (filled by Pulse module)
    eval_scores: dict[str, float] | None = None
    safety_flags: list[str] | None = None

    # General
    status: str = "ok"  # ok | error
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_llm_call(cls, provider: str, kwargs: dict[str, Any]) -> SpanData:
        model = kwargs.get("model", "unknown")
        return cls(
            span_type="llm",
            provider=provider,
            model=model,
            model_requested=model,
            name=f"{provider}.chat.create",
        )

    @classmethod
    def from_tool_call(cls, tool_name: str, run_id: str = "") -> SpanData:
        return cls(
            span_type="tool",
            name=f"tool.{tool_name}",
            tool_name=tool_name,
            run_id=run_id,
        )

    def complete(self, tokens_in: int, tokens_out: int) -> None:
        from reins.core.pricing import get_price

        self.ended_at = _utcnow()
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost = get_price(self.provider, self.model, tokens_in, tokens_out)

    def complete_from_anthropic(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage:
            self.complete(
                tokens_in=getattr(usage, "input_tokens", 0),
                tokens_out=getattr(usage, "output_tokens", 0),
            )
        else:
            self.ended_at = _utcnow()
            self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000

    def complete_from_openai(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage:
            self.complete(
                tokens_in=getattr(usage, "prompt_tokens", 0),
                tokens_out=getattr(usage, "completion_tokens", 0),
            )
        else:
            self.ended_at = _utcnow()
            self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000

    def complete_from_usage_dict(self, usage: dict[str, int]) -> None:
        self.complete(
            tokens_in=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            tokens_out=usage.get("output_tokens", usage.get("completion_tokens", 0)),
        )

    def mark_error(self, error: Exception) -> None:
        self.ended_at = _utcnow()
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        self.status = "error"
        self.error_message = str(error)

    def to_row(self) -> tuple:
        import json

        return (
            self.span_id,
            self.run_id,
            self.parent_span_id,
            self.span_type,
            self.name,
            self.started_at.isoformat(),
            self.ended_at.isoformat() if self.ended_at else None,
            self.duration_ms,
            self.model,
            self.model_requested,
            self.provider,
            self.tokens_in,
            self.tokens_out,
            float(self.cost),
            self.degraded,
            self.tool_name,
            self.tool_status,
            self.tool_error,
            self.context_tokens,
            self.context_health,
            json.dumps(self.eval_scores) if self.eval_scores else None,
            json.dumps(self.safety_flags) if self.safety_flags else None,
            self.status,
            self.error_message,
            json.dumps(self.metadata) if self.metadata else None,
        )


@dataclass
class RunData:
    """A complete Agent execution (one or more spans)."""

    run_id: str = field(default_factory=_uuid)
    session_id: str | None = None
    agent_name: str = "default"
    status: str = "running"  # running | completed | failed | budget_exceeded
    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    total_cost: Decimal = field(default_factory=lambda: Decimal("0"))
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    budget_limit: Decimal | None = None
    degraded_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def complete(self, status: str = "completed") -> None:
        self.ended_at = _utcnow()
        self.status = status

    def add_span_cost(self, span: SpanData) -> None:
        self.total_cost += span.cost
        self.total_tokens_in += span.tokens_in
        self.total_tokens_out += span.tokens_out
        if span.degraded:
            self.degraded_count += 1

    def to_row(self) -> tuple:
        import json

        return (
            self.run_id,
            self.session_id,
            self.agent_name,
            self.status,
            self.started_at.isoformat(),
            self.ended_at.isoformat() if self.ended_at else None,
            float(self.total_cost),
            self.total_tokens_in,
            self.total_tokens_out,
            float(self.budget_limit) if self.budget_limit else None,
            self.degraded_count,
            json.dumps(self.metadata) if self.metadata else None,
        )
