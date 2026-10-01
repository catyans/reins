"""Explicit list-price estimates for text-only Gemini OpenAI-compatible usage.

This does not reconcile the runtime's pending ledger or claim an actual billing
debit. Callers must exclude hosted tools, explicit cache storage and multimodal
requests, whose additional charges are outside this estimator.
"""

from dataclasses import dataclass
from decimal import Decimal


class IncompleteGoogleUsage(ValueError):
    pass


@dataclass(frozen=True)
class GoogleTextPrices:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal

    def __post_init__(self):
        for v in self.__dict__.values():
            if not isinstance(v, Decimal) or not v.is_finite() or v < 0:
                raise ValueError("Supply explicit nonnegative Decimal rates")


def estimate_google_text_cost(usage: dict, prices: GoogleTextPrices) -> Decimal:
    if not isinstance(usage, dict):
        raise IncompleteGoogleUsage("Missing usage")
    incoming = usage.get("prompt_tokens")
    outgoing = usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or {}
    if not isinstance(details, dict) or not isinstance(output_details, dict):
        raise IncompleteGoogleUsage("Malformed token details")
    cached = details.get("cached_tokens") or 0
    if any(type(n) is not int or n < 0 for n in (incoming, outgoing, cached)):
        raise IncompleteGoogleUsage("Missing or invalid token counts")
    if cached > incoming:
        raise IncompleteGoogleUsage("Cached input exceeds total input")
    if any(details.get(k) for k in ("audio_tokens", "image_tokens", "cache_write_tokens")):
        raise IncompleteGoogleUsage("Non-text/cache-write billing is outside this estimator")
    if output_details.get("audio_tokens"):
        raise IncompleteGoogleUsage("Non-text output billing is outside this estimator")
    # Completion total already includes reasoning tokens; do not add them twice.
    return (
        (incoming - cached) * prices.input_per_million
        + cached * prices.cached_input_per_million
        + outgoing * prices.output_per_million
    ) / Decimal(1000000)
