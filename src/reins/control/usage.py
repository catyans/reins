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


def estimate_components(*, system="", history="", retrieval="", tools=None, user=""):
    """Local size estimates only; never substitute these for provider usage/bounds."""
    import json

    values = {
        "system": system,
        "history": history,
        "retrieval": retrieval,
        "tool_schema": tools or [],
        "user": user,
    }
    return {
        k: (len((v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)).encode()) + 3) // 4
        if v
        else 0
        for k, v in values.items()
    }


def prepare_context(
    *,
    user,
    system="",
    history="",
    retrieval="",
    tools=(),
    allowed_tools=(),
    max_retrieval_chars=12000,
):
    """Explicit stage-specific selection. Does not summarize or silently rewrite history."""
    if type(max_retrieval_chars) is not int or max_retrieval_chars < 0:
        raise ValueError("Invalid retrieval bound")
    selected = [t for t in tools if t.get("function", t).get("name") in allowed_tools]
    clipped = retrieval[:max_retrieval_chars]
    return {
        "user": user,
        "system": system,
        "history": history,
        "retrieval": clipped,
        "tools": selected,
        "retrieval_truncated": len(clipped) != len(retrieval),
        "component_estimates": estimate_components(
            user=user, system=system, history=history, retrieval=clipped, tools=selected
        ),
    }


def context_revision_diff(before, after):
    """Compare estimates separately from measured provider totals."""
    return {k: after.get(k, 0) - before.get(k, 0) for k in sorted(COMPONENTS)}
