"""Deterministic trajectory contracts complement business-output validators."""

from __future__ import annotations

import html
import json
from decimal import Decimal
from pathlib import Path


def evaluate_case(case, contract):
    """Missing measurements fail a requested gate, including a missing trajectory."""
    failures = []
    events = case.get("trajectory")
    if not isinstance(events, list):
        events = []
        failures.append("Missing execution trajectory")
    tools = [e.get("tool") for e in events if e.get("kind") == "tool"]
    for tool in contract.get("required_tools", []):
        if tool not in tools:
            failures.append(f"Required tool missing: {tool}")
    allowed = contract.get("allowed_tools")
    if allowed is not None and any(t not in allowed for t in tools):
        failures.append("Unexpected tool executed")
    cursor = 0
    for tool in contract.get("ordered_tools", []):
        try:
            cursor = tools.index(tool, cursor) + 1
        except ValueError:
            failures.append("Required tool order not followed")
            break
    if "max_tool_calls" in contract and len(tools) > contract["max_tool_calls"]:
        failures.append("Tool count exceeded")
    for check in contract.get("required_validations", []):
        if not any(
            e.get("kind") == "validation" and e.get("name") == check and e.get("passed") is True
            for e in events
        ):
            failures.append(f"Validation missing or failed: {check}")
    for e in events:
        if e.get("kind") == "tool" and e.get("tool") in contract.get("approval_tools", []):
            if not e.get("approval_id") or e.get("approval_valid") is not True:
                failures.append(f"Valid approval missing: {e.get('tool')}")
    for field, limit in (("cost", "max_cost"), ("latency_ms", "max_latency_ms")):
        if limit in contract:
            try:
                value = Decimal(str(case[field]))
                maximum = Decimal(str(contract[limit]))
                if not value.is_finite() or value < 0 or not maximum.is_finite() or maximum < 0:
                    raise ValueError("Invalid measurement")
                if value > maximum:
                    failures.append(f"{field} exceeded")
            except (KeyError, ValueError, ArithmeticError):
                failures.append(f"{field} missing or invalid")
    if case.get("accepted") is not True:
        failures.append("Business acceptance failed or missing")
    return {"case_id": case["case_id"], "passed": not failures, "failures": failures}


def evaluate_suite(cases, contract, *, expected_case_ids):
    expected = list(expected_case_ids)
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids) or len(set(expected)) != len(expected):
        raise ValueError("Duplicate case identity")
    if set(ids) != set(expected):
        raise ValueError("Missing or unexpected cases in frozen suite")
    if not expected:
        raise ValueError("Empty regression suite")
    results = [evaluate_case(c, contract) for c in cases]
    return {
        "passed": all(r["passed"] for r in results),
        "cases": results,
        "total": len(results),
        "passing": sum(r["passed"] for r in results),
    }


def write_report(report, path):
    """Always provide both readable HTML and structured JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    rows = "".join(
        "<tr><td>"
        + html.escape(str(r["case_id"]))
        + "</td><td>"
        + ("Passed" if r["passed"] else "Needs attention")
        + "</td><td>"
        + html.escape("; ".join(r["failures"]))
        + "</td></tr>"
        for r in report["cases"]
    )
    path.with_suffix(".html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Reins regression report</title>'
        "<style>body{font:16px system-ui;max-width:1100px;margin:3em auto;padding:1em;"
        "color:#203d2b}table{border-collapse:collapse;width:100%}td,th{padding:1em;"
        "text-align:left;border-bottom:1px solid #ddd}</style><h1>Execution regression report</h1>"
        f"<p>{report['passing']} / {report['total']} cases passed the frozen contract.</p>"
        "<table><tr><th>Case</th><th>Result</th><th>Details</th></tr>" + rows + "</table></html>",
        encoding="utf-8",
    )
