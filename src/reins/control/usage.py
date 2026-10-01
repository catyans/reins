"""Explicit input attribution; missing components are never called waste."""

COMPONENTS = {"system", "history", "retrieval", "tool_schema", "user"}


def token_attribution(input_tokens, output_tokens, **components):
    """Use counts from the caller's tokenizer; preserve unassigned framing tokens.

    These are attribution measurements, not provider billing line items.
    No proportional allocation or inference of 'wasted' tokens is performed.
    """
    if set(components) - COMPONENTS:
        raise ValueError("Unknown input component")
    if any(
        type(v) is not int or v < 0 for v in (input_tokens, output_tokens, *components.values())
    ):
        raise ValueError("Token counts must be nonnegative integers")
    if sum(components.values()) > input_tokens:
        raise ValueError("Components exceed the measured input total")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        **components,
        "unattributed": input_tokens - sum(components.values()),
    }
