"""Model pricing table. Updated periodically."""

from __future__ import annotations

from decimal import Decimal

# (input_price_per_million_tokens, output_price_per_million_tokens)
PRICING: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
    "anthropic": {
        "claude-opus-4-20250514": (Decimal("15.0"), Decimal("75.0")),
        "claude-sonnet-4-20250514": (Decimal("3.0"), Decimal("15.0")),
        "claude-haiku-4-20250514": (Decimal("0.80"), Decimal("4.0")),
        # Aliases
        "claude-opus-4": (Decimal("15.0"), Decimal("75.0")),
        "claude-sonnet-4": (Decimal("3.0"), Decimal("15.0")),
        "claude-haiku-4": (Decimal("0.80"), Decimal("4.0")),
        # Older models
        "claude-3-5-sonnet-20241022": (Decimal("3.0"), Decimal("15.0")),
        "claude-3-5-haiku-20241022": (Decimal("0.80"), Decimal("4.0")),
        "claude-3-opus-20240229": (Decimal("15.0"), Decimal("75.0")),
    },
    "openai": {
        "gpt-4o": (Decimal("2.50"), Decimal("10.0")),
        "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
        "gpt-4-turbo": (Decimal("10.0"), Decimal("30.0")),
        "o3": (Decimal("10.0"), Decimal("40.0")),
        "o3-mini": (Decimal("1.10"), Decimal("4.40")),
        "o4-mini": (Decimal("1.10"), Decimal("4.40")),
    },
    "google": {
        "gemini-2.5-pro": (Decimal("1.25"), Decimal("10.0")),
        "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
        "gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
        "gemini-2.0-flash": (Decimal("0.10"), Decimal("0.40")),
    },
}

_M = Decimal("1000000")


class UnknownPriceError(ValueError):
    """An unpriced model must never be silently counted as free."""


def get_rates(provider, model, overrides=None):
    custom = (overrides or {}).get(provider, {}).get(model)
    rates = (
        tuple(Decimal(str(x)) for x in custom)
        if custom is not None
        else PRICING.get(provider, {}).get(model)
    )
    if rates is None:
        raise UnknownPriceError(
            f"No exact price for {provider}/{model}; configure prices explicitly"
        )
    return rates


def get_price(
    provider: str, model: str, tokens_in: int, tokens_out: int, overrides=None
) -> Decimal:
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("Token counts must be nonnegative")
    incoming, outgoing = get_rates(provider, model, overrides)
    return ((incoming * tokens_in + outgoing * tokens_out) / _M).quantize(Decimal("0.000000000001"))


def get_model_cost_tier(provider: str, model: str) -> Decimal:
    """Get the input cost per million tokens for ranking models by cost."""
    prices = PRICING.get(provider, {}).get(model)
    if prices is None:
        return Decimal("0")
    return prices[0]
