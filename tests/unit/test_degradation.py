"""Tests for model degradation mapping."""

from reins.budget.degradation import get_cheaper_model, get_degradation_chain


def test_anthropic_degradation():
    assert get_cheaper_model("anthropic", "claude-opus-4-20250514") == "claude-sonnet-4-20250514"
    assert get_cheaper_model("anthropic", "claude-sonnet-4-20250514") == "claude-haiku-4-20250514"
    assert get_cheaper_model("anthropic", "claude-haiku-4-20250514") is None


def test_openai_degradation():
    assert get_cheaper_model("openai", "o3") == "gpt-4o"
    assert get_cheaper_model("openai", "gpt-4o") == "gpt-4o-mini"
    assert get_cheaper_model("openai", "gpt-4o-mini") is None


def test_unknown_model():
    assert get_cheaper_model("anthropic", "nonexistent") is None
    assert get_cheaper_model("unknown", "model") is None


def test_degradation_chain():
    chain = get_degradation_chain("anthropic", "claude-opus-4-20250514")
    assert chain == [
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-20250514",
    ]


def test_degradation_chain_single():
    chain = get_degradation_chain("anthropic", "claude-haiku-4-20250514")
    assert chain == ["claude-haiku-4-20250514"]
