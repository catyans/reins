"""Tests for trace visualizer."""

from reins.trace.visualizer import (
    format_cost,
    format_duration,
    format_tokens,
    render_call_tree,
    render_run_list,
    render_span_detail,
)


def test_format_cost():
    assert format_cost(0.0001) == "$0.000100"
    assert format_cost(0.05) == "$0.0500"
    assert format_cost(1.5) == "$1.50"


def test_format_duration():
    assert format_duration(500) == "500ms"
    assert format_duration(2500) == "2.5s"
    assert format_duration(90000) == "1.5m"


def test_format_tokens():
    assert format_tokens(1000, 500) == "1000→500 tok"


def test_render_run_list_empty():
    result = render_run_list([])
    assert "No runs found" in result


def test_render_run_list():
    runs = [
        {
            "run_id": "abc123def456",
            "agent_name": "test_agent",
            "status": "completed",
            "total_cost": 0.05,
            "span_count": 3,
            "degraded_count": 1,
            "started_at": "2026-04-03T10:00:00",
        }
    ]
    result = render_run_list(runs)
    assert "test_agent" in result
    assert "completed" in result


def test_render_call_tree():
    run = {
        "run_id": "abc123",
        "agent_name": "test_agent",
        "status": "completed",
        "total_cost": 0.01,
        "degraded_count": 0,
    }
    spans = [
        {
            "span_id": "s1",
            "run_id": "abc123",
            "parent_span_id": None,
            "span_type": "llm",
            "name": "anthropic.chat.create",
            "model": "claude-sonnet-4",
            "model_requested": "claude-sonnet-4",
            "degraded": False,
            "tokens_in": 100,
            "tokens_out": 50,
            "cost": 0.001,
            "duration_ms": 500,
            "status": "ok",
            "context_health": None,
            "error_message": None,
            "tool_name": None,
        }
    ]
    result = render_call_tree(run, spans)
    assert "test_agent" in result
    assert "anthropic.chat.create" in result
    assert "1 spans" in result


def test_render_call_tree_empty():
    run = {
        "run_id": "abc",
        "agent_name": "test",
        "status": "completed",
        "total_cost": 0,
        "degraded_count": 0,
    }
    result = render_call_tree(run, [])
    assert "No spans" in result


def test_render_span_detail():
    span = {
        "span_id": "s1",
        "span_type": "llm",
        "name": "test",
        "provider": "anthropic",
        "model": "claude-sonnet-4",
        "tokens_in": 100,
        "tokens_out": 50,
        "cost": 0.001,
        "duration_ms": 500,
        "status": "ok",
        "degraded": False,
        "model_requested": None,
        "context_tokens": None,
        "context_health": None,
        "error_message": None,
        "started_at": "2026-04-03T10:00:00",
        "ended_at": "2026-04-03T10:00:01",
    }
    result = render_span_detail(span)
    assert "anthropic" in result
    assert "claude-sonnet-4" in result
