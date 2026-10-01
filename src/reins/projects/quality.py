"""Versioned offline quality contracts; factual checks are never optional."""

from reins.projects.documents import FIELDS
from reins.projects.runtime import valid_field

SUMMARY_CONTRACT = "source-backed-summary-v1"


def prose_supported(answer, sources):
    """Preserve words, punctuation, case and code; normalize only prose whitespace.

    A summary may differ from its quote, but the separate semantic judge must
    approve it. This helper alone never establishes factual correctness.
    """
    if not isinstance(answer, dict) or set(answer) != {"value", "source", "quote"}:
        return False
    value, source, quote = (answer[k] for k in ("value", "source", "quote"))
    if value is None:
        return source is None and quote is None
    if not (
        isinstance(value, str)
        and 0 < len(value) <= 1200
        and isinstance(source, str)
        and source in sources
        and isinstance(quote, str)
        and 0 < len(quote) <= 2000
    ):
        return False
    clean = " ".join(quote.split())
    return bool(clean) and clean in " ".join(sources[source].split())


def assess(fields, views, semantic_scores, *, contract="verbatim-v1"):
    """Apply the same explicit contract to every arm, retaining original scores."""
    if contract not in ("verbatim-v1", SUMMARY_CONTRACT):
        raise ValueError("Unknown quality contract")
    judged = (
        isinstance(semantic_scores, dict)
        and set(semantic_scores) == set(FIELDS)
        and all(type(v) is bool for v in semantic_scores.values())
    )
    result = {}
    for field in FIELDS:
        answer = fields.get(field)
        support = (
            prose_supported(answer, views[field])
            if contract == SUMMARY_CONTRACT and field in ("purpose", "release_note")
            else valid_field(field, answer, views[field])
        )
        result[field] = bool(judged and semantic_scores[field] and support)
    return {
        "contract": contract,
        "judge_complete": judged,
        "fields": result,
        "accepted": all(result.values()),
    }
