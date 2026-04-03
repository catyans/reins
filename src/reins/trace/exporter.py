"""Export traces to OTLP JSON format for Grafana/Datadog/etc."""

from __future__ import annotations

import json
from typing import Any


def spans_to_otlp_json(spans: list[dict[str, Any]]) -> str:
    """Convert Reins spans to OTLP JSON trace format.

    Follows the OpenTelemetry Protocol JSON encoding:
    https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding
    """
    otlp_spans = []

    for span in spans:
        otlp_span = {
            "traceId": _to_hex_id(span.get("run_id", "")),
            "spanId": _to_hex_id(span.get("span_id", "")),
            "name": span.get("name", "unknown"),
            "kind": _span_kind(span.get("span_type", "custom")),
            "startTimeUnixNano": _iso_to_nanos(span.get("started_at", "")),
            "endTimeUnixNano": _iso_to_nanos(span.get("ended_at", "")),
            "attributes": _build_attributes(span),
            "status": {
                "code": 2 if span.get("status") == "error" else 1,  # ERROR=2, OK=1
                "message": span.get("error_message", ""),
            },
        }

        parent = span.get("parent_span_id")
        if parent:
            otlp_span["parentSpanId"] = _to_hex_id(parent)

        otlp_spans.append(otlp_span)

    # OTLP envelope
    resource_spans = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "reins"}},
                        {"key": "service.version", "value": {"stringValue": "0.1.0"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "reins", "version": "0.1.0"},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }

    return json.dumps(resource_spans, indent=2, default=str)


def _build_attributes(span: dict[str, Any]) -> list[dict]:
    """Build OTLP attributes from span data.

    Follows OpenTelemetry GenAI semantic conventions where applicable.
    """
    attrs = []

    def _add(key: str, value: Any, vtype: str = "stringValue") -> None:
        if value is not None and value != "" and value != 0:
            attrs.append({"key": key, "value": {vtype: str(value) if vtype == "stringValue" else value}})

    # GenAI semantic conventions
    _add("gen_ai.system", span.get("provider"))
    _add("gen_ai.request.model", span.get("model_requested") or span.get("model"))
    _add("gen_ai.response.model", span.get("model"))
    _add("gen_ai.usage.input_tokens", span.get("tokens_in"), "intValue")
    _add("gen_ai.usage.output_tokens", span.get("tokens_out"), "intValue")

    # Reins-specific attributes
    _add("reins.span_type", span.get("span_type"))
    _add("reins.cost", span.get("cost"), "doubleValue")
    _add("reins.degraded", span.get("degraded"), "boolValue")

    if span.get("tool_name"):
        _add("reins.tool.name", span.get("tool_name"))
        _add("reins.tool.status", span.get("tool_status"))

    if span.get("context_health") is not None:
        _add("reins.context_health", span.get("context_health"), "doubleValue")

    if span.get("context_tokens"):
        _add("reins.context_tokens", span.get("context_tokens"), "intValue")

    return attrs


def _span_kind(span_type: str) -> int:
    """Map Reins span type to OTLP SpanKind."""
    # CLIENT=3, INTERNAL=1
    if span_type == "llm":
        return 3  # CLIENT (calling external LLM API)
    if span_type == "tool":
        return 3  # CLIENT (calling external tool)
    return 1  # INTERNAL


def _to_hex_id(uuid_str: str) -> str:
    """Convert UUID to 16-char hex trace/span ID."""
    return uuid_str.replace("-", "")[:16].ljust(16, "0")


def _iso_to_nanos(iso_str: str) -> int:
    """Convert ISO timestamp string to nanoseconds since epoch."""
    if not iso_str:
        return 0
    try:
        from datetime import datetime, timezone

        # Handle various ISO formats
        iso_str = iso_str.replace("Z", "+00:00")
        if "+" not in iso_str and iso_str.count("-") <= 2:
            iso_str += "+00:00"
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return 0
