"""Tests for context health scoring."""

from reins.lens.context_health import (
    compute_health_scores,
    get_context_window,
    render_health_report,
)


def test_get_context_window():
    assert get_context_window("claude-sonnet-4-20250514") == 200_000
    assert get_context_window("gpt-4o") == 128_000
    assert get_context_window("unknown-model") == 200_000


def test_compute_health_scores_empty():
    assert compute_health_scores([]) == []


def test_compute_health_scores_single_span():
    spans = [{"tokens_in": 1000, "tokens_out": 200, "model": "claude-sonnet-4"}]
    result = compute_health_scores(spans)
    assert len(result) == 1
    assert 0 <= result[0]["_health_score"] <= 1
    assert result[0]["_health_score"] > 0.8  # Low utilization = healthy


def test_health_degrades_with_high_utilization():
    """Spans with increasing context usage should show declining health."""
    spans = []
    for i in range(10):
        # Simulating growing context: 10k, 20k, ... 100k tokens
        tokens_in = (i + 1) * 10_000
        spans.append(
            {
                "tokens_in": tokens_in,
                "tokens_out": 500,
                "model": "claude-sonnet-4",
            }
        )

    result = compute_health_scores(spans)
    # First span should be healthier than last
    assert result[0]["_health_score"] > result[-1]["_health_score"]


def test_health_detects_repetition():
    """Repeated identical input sizes should trigger duplication signal."""
    spans = [
        {"tokens_in": 5000, "tokens_out": 200, "model": "claude-sonnet-4"},
        {"tokens_in": 5000, "tokens_out": 200, "model": "claude-sonnet-4"},
        {"tokens_in": 5000, "tokens_out": 200, "model": "claude-sonnet-4"},
        {"tokens_in": 5000, "tokens_out": 200, "model": "claude-sonnet-4"},
    ]
    result = compute_health_scores(spans)
    # Last span should have lower duplication score
    assert result[-1]["_duplication_score"] < 1.0


def test_render_health_report():
    run = {"run_id": "abc123", "agent_name": "test"}
    spans = [
        {
            "tokens_in": 1000,
            "tokens_out": 200,
            "model": "claude-sonnet-4",
            "status": "ok",
            "cost": 0.001,
            "duration_ms": 500,
        },
        {
            "tokens_in": 5000,
            "tokens_out": 300,
            "model": "claude-sonnet-4",
            "status": "ok",
            "cost": 0.003,
            "duration_ms": 800,
        },
    ]
    result = render_health_report(run, spans)
    assert "Context Health Report" in result
    assert "test" in result


def test_render_health_report_empty():
    result = render_health_report({}, [])
    assert "No spans" in result
