"""Narrow deterministic calculation, driven only by explicit source evidence."""

import re
from decimal import Decimal


def calculate_amount(payload, answer):
    if not isinstance(answer, dict):
        return answer
    source = payload["source"]
    # This rule is applicable only to a fully specified, tax-free payable sum.
    # Explicit final totals, credits, ambiguous repeated amounts and taxes abstain.
    if "Compute amount due." not in source or "No tax applies." not in source:
        return answer
    values = []
    for label in ("Subtotal", "discount", "shipping"):
        matches = re.findall(r"\b" + label + r":\s*(\d+\.\d{2})\b", source)
        if len(matches) != 1:
            return answer
        values.append(Decimal(matches[0]))
    value = values[0] - values[1] + values[2]
    if value < 0:
        return answer
    return {**answer, "amount_due": f"{value:.2f}"}
