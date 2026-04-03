"""Model degradation mapping."""

from __future__ import annotations

# provider -> {expensive_model: cheaper_model}
DEGRADATION_MAP: dict[str, dict[str, str]] = {
    "anthropic": {
        "claude-opus-4-20250514": "claude-sonnet-4-20250514",
        "claude-opus-4": "claude-sonnet-4",
        "claude-sonnet-4-20250514": "claude-haiku-4-20250514",
        "claude-sonnet-4": "claude-haiku-4",
        "claude-3-5-sonnet-20241022": "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229": "claude-3-5-sonnet-20241022",
    },
    "openai": {
        "o3": "gpt-4o",
        "gpt-4o": "gpt-4o-mini",
        "gpt-4-turbo": "gpt-4o-mini",
    },
    "google": {
        "gemini-2.5-pro": "gemini-2.5-flash",
        "gemini-2.5-flash": "gemini-2.0-flash",
    },
}


def get_cheaper_model(provider: str, model: str) -> str | None:
    """Get the next cheaper model for the given provider/model."""
    return DEGRADATION_MAP.get(provider, {}).get(model)


def get_degradation_chain(provider: str, model: str) -> list[str]:
    """Get the full degradation chain starting from the given model."""
    chain = [model]
    current = model
    while True:
        cheaper = get_cheaper_model(provider, current)
        if cheaper is None or cheaper in chain:
            break
        chain.append(cheaper)
        current = cheaper
    return chain
