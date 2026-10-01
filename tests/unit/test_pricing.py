"""Tests for pricing calculations."""

from decimal import Decimal

import pytest

from reins.core.pricing import UnknownPriceError, get_price


def test_anthropic_sonnet_pricing():
    cost = get_price("anthropic", "claude-sonnet-4-20250514", 1000, 500)
    # input: 3.0 * 1000 / 1M = 0.003
    # output: 15.0 * 500 / 1M = 0.0075
    expected = Decimal("0.003") + Decimal("0.0075")
    assert cost == expected.quantize(Decimal("0.000001"))


def test_anthropic_haiku_pricing():
    cost = get_price("anthropic", "claude-haiku-4-20250514", 1000, 500)
    # input: 0.80 * 1000 / 1M = 0.0008
    # output: 4.0 * 500 / 1M = 0.002
    expected = Decimal("0.0008") + Decimal("0.002")
    assert cost == expected.quantize(Decimal("0.000001"))


def test_openai_gpt4o_pricing():
    cost = get_price("openai", "gpt-4o", 1000, 500)
    # input: 2.50 * 1000 / 1M = 0.0025
    # output: 10.0 * 500 / 1M = 0.005
    expected = Decimal("0.0025") + Decimal("0.005")
    assert cost == expected.quantize(Decimal("0.000001"))


def test_unknown_model_requires_explicit_price():
    with pytest.raises(UnknownPriceError):
        get_price("anthropic", "unknown-model", 1000, 500)


def test_unknown_provider_requires_explicit_price():
    with pytest.raises(UnknownPriceError):
        get_price("unknown-provider", "model", 1000, 500)


def test_zero_tokens():
    cost = get_price("anthropic", "claude-sonnet-4-20250514", 0, 0)
    assert cost == Decimal("0.000000")


def test_large_token_count():
    cost = get_price("anthropic", "claude-sonnet-4-20250514", 100_000, 50_000)
    # input: 3.0 * 100k / 1M = 0.3
    # output: 15.0 * 50k / 1M = 0.75
    assert cost == Decimal("1.050000")
