"""Tests for OTel OTLP exporter."""

import json

from reins.trace.exporter import spans_to_otlp_json


def test_export_empty():
    result = spans_to_otlp_json([])
    data = json.loads(result)
    assert "resourceSpans" in data
    assert data["resourceSpans"][0]["scopeSpans"][0]["spans"] == []


def test_export_single_span():
    spans = [
        {
            "run_id": "run-123",
            "span_id": "span-456",
            "parent_span_id": None,
            "name": "anthropic.chat.create",
            "span_type": "llm",
            "provider": "anthropic",
            "model": "claude-sonnet-4",
            "model_requested": "claude-sonnet-4",
            "tokens_in": 1000,
            "tokens_out": 500,
            "cost": 0.01,
            "degraded": False,
            "status": "ok",
            "error_message": "",
            "started_at": "2026-04-03T10:00:00+00:00",
            "ended_at": "2026-04-03T10:00:01+00:00",
            "tool_name": None,
            "tool_status": None,
            "context_health": None,
            "context_tokens": None,
        }
    ]
    result = spans_to_otlp_json(spans)
    data = json.loads(result)

    otlp_spans = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(otlp_spans) == 1

    span = otlp_spans[0]
    assert span["name"] == "anthropic.chat.create"
    assert span["kind"] == 3  # CLIENT
    assert span["status"]["code"] == 1  # OK

    # Check GenAI attributes
    attr_keys = [a["key"] for a in span["attributes"]]
    assert "gen_ai.system" in attr_keys
    assert "gen_ai.request.model" in attr_keys
    assert "reins.cost" in attr_keys


def test_export_error_span():
    spans = [
        {
            "run_id": "run-err",
            "span_id": "span-err",
            "parent_span_id": None,
            "name": "failed_call",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "model_requested": "gpt-4o",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0,
            "degraded": False,
            "status": "error",
            "error_message": "API timeout",
            "started_at": "2026-04-03T10:00:00",
            "ended_at": "2026-04-03T10:00:05",
            "tool_name": None,
            "tool_status": None,
            "context_health": None,
            "context_tokens": None,
        }
    ]
    result = spans_to_otlp_json(spans)
    data = json.loads(result)
    span = data["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["status"]["code"] == 2  # ERROR
    assert span["status"]["message"] == "API timeout"


def test_export_degraded_span():
    spans = [
        {
            "run_id": "run-deg",
            "span_id": "span-deg",
            "parent_span_id": None,
            "name": "degraded_call",
            "span_type": "llm",
            "provider": "anthropic",
            "model": "claude-haiku-4",
            "model_requested": "claude-sonnet-4",
            "tokens_in": 500,
            "tokens_out": 200,
            "cost": 0.001,
            "degraded": True,
            "status": "ok",
            "error_message": "",
            "started_at": "2026-04-03T10:00:00+00:00",
            "ended_at": "2026-04-03T10:00:01+00:00",
            "tool_name": None,
            "tool_status": None,
            "context_health": 0.85,
            "context_tokens": 5000,
        }
    ]
    result = spans_to_otlp_json(spans)
    data = json.loads(result)
    span = data["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert "reins.degraded" in attrs
    assert "reins.context_health" in attrs
    assert attrs["gen_ai.request.model"]["stringValue"] == "claude-sonnet-4"
    assert attrs["gen_ai.response.model"]["stringValue"] == "claude-haiku-4"
