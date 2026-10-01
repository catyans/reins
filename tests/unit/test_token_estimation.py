"""Tests for token estimation from request kwargs."""

from reins.core.models import SpanData, _estimate_input_tokens


def test_estimate_from_simple_message():
    kwargs = {"messages": [{"role": "user", "content": "Hello world"}]}
    est = _estimate_input_tokens("anthropic", kwargs)
    # "Hello world" = 11 chars / 4 = 2, but min is 50
    assert est == 50


def test_estimate_from_long_message():
    long_text = "x" * 4000  # 4000 chars ≈ 1000 tokens
    kwargs = {"messages": [{"role": "user", "content": long_text}]}
    est = _estimate_input_tokens("anthropic", kwargs)
    assert est == 1000


def test_estimate_with_system_prompt():
    kwargs = {
        "system": "You are a helpful assistant. " * 10,  # ~300 chars
        "messages": [{"role": "user", "content": "Hi"}],
    }
    est = _estimate_input_tokens("anthropic", kwargs)
    assert est > 50  # System prompt contributes


def test_estimate_multi_turn():
    kwargs = {
        "messages": [
            {"role": "user", "content": "a" * 400},
            {"role": "assistant", "content": "b" * 400},
            {"role": "user", "content": "c" * 400},
        ]
    }
    est = _estimate_input_tokens("anthropic", kwargs)
    # 1200 chars / 4 = 300 tokens
    assert est == 300


def test_estimate_content_blocks():
    kwargs = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "a" * 800},
                ],
            },
        ]
    }
    est = _estimate_input_tokens("anthropic", kwargs)
    assert est == 200  # 800 / 4


def test_span_from_llm_call_carries_estimates():
    kwargs = {
        "model": "claude-sonnet-4",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": "x" * 2000}],
    }
    span = SpanData.from_llm_call("anthropic", kwargs)
    assert span.estimated_input_tokens == 500  # 2000 / 4
    assert span.estimated_max_output_tokens == 2048
