"""Audit real-call output, report frozen-policy test performance, plot artifacts."""

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

NAMES = {
    "fixed-flash-v1": "Fixed Flash",
    "fixed-lite-v1": "Fixed Flash-Lite",
    "input-rule-v1": "Input rules",
    "validated-cascade-v1": "Validated cascade",
}


def percentile(values, p):
    return sorted(values)[min(len(values) - 1, math.ceil(p * len(values)) - 1)]


def analyze(folder):
    protocol = json.loads((folder / "protocol.json").read_text())
    summary = json.loads((folder / "summary.json").read_text())
    fixtures = json.loads((folder / "cases.json").read_text())
    calls = [json.loads(line) for line in (folder / "calls.jsonl").read_text().splitlines()]
    grouped = defaultdict(list)
    total = 0
    for c in calls:
        usage = c.get("usage")
        cost = None
        if usage:
            incoming, outgoing = map(float, protocol["prices_per_million"][c["model"]])
            cost = (usage["prompt_tokens"] * incoming + usage["completion_tokens"] * outgoing) / 1e6
            total += cost
        c["list_price_cost"] = cost
        grouped[(c["split"], c["policy"], c["case_id"])].append(c)
    reports = summary["reports"]
    details = {}
    for split, cases in fixtures.items():
        if split not in reports:
            continue
        for policy in protocol["policy_names"]:
            records = []
            for case in cases:
                attempts = grouped.get((split, policy, case["id"]), [])
                last = attempts[-1] if attempts else {}
                try:
                    answer = json.loads(last.get("answer") or "null")
                except json.JSONDecodeError:
                    answer = None
                success = isinstance(answer, dict) and all(
                    k in answer and answer[k] == v for k, v in case["expected"].items()
                )
                records.append(
                    {
                        "case_id": case["id"],
                        "category": case["input"]["category"],
                        "success": success,
                        "cost": sum(c["list_price_cost"] or 0 for c in attempts),
                        "attempts": len(attempts),
                        "seconds": sum(c["seconds"] for c in attempts),
                        "complete_cost": bool(attempts)
                        and all(c["list_price_cost"] is not None for c in attempts),
                    }
                )
            details[f"{split}/{policy}"] = records
    selected = reports.get("validation", {}).get("recommendation")
    paired = None
    if selected and "test" in reports:
        base = details["test/fixed-flash-v1"]
        chosen = details["test/" + selected]
        if all(r["complete_cost"] for r in base + chosen):
            rng = random.Random(729)
            quality = []
            saving = []
            for _ in range(5000):
                ids = [rng.randrange(len(base)) for _ in base]
                bs = sum(base[i]["success"] for i in ids)
                cs = sum(chosen[i]["success"] for i in ids)
                quality.append((cs - bs) / len(ids))
                if bs and cs:
                    bc = sum(base[i]["cost"] for i in ids) / bs
                    cc = sum(chosen[i]["cost"] for i in ids) / cs
                    if bc:
                        saving.append(1 - cc / bc)
            paired = {
                "method": (
                    "Paired case bootstrap, 5000 resamples; descriptive, not non-inferiority proof"
                ),
                "quality_difference_interval": [
                    percentile(quality, 0.025),
                    percentile(quality, 0.975),
                ],
                "relative_cost_saving_interval": [
                    percentile(saving, 0.025),
                    percentile(saving, 0.975),
                ]
                if saving
                else None,
            }
    result = {
        "real_api_calls": len(calls),
        "estimated_total_usd": total,
        "frozen_validation_policy": selected,
        "bootstrap": paired,
        "reports": reports,
        "case_details": details,
        "provider_errors": summary["provider_errors"],
        "limitation": (
            "Real APIs, constructed invoices; shared templates across splits. "
            "No customer ROI established."
        ),
    }
    # Independent raw-usage audit against the SDK ledger reports.
    mismatches = []
    for split, report in reports.items():
        for row in report["candidates"]:
            items = details[split + "/" + row["policy_version"]]
            if abs(sum(r["cost"] for r in items) - row["known_total_cost"]) > 1e-8:
                mismatches.append(split + "/" + row["policy_version"])
    result["ledger_cost_mismatches"] = mismatches
    (folder / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Reins: real API evaluation",
        "",
        (
            "Real Gemini calls on a constructed invoice-extraction workload. This is "
            "not customer data."
        ),
        "",
        f"Calls: **{len(calls)}**. Total estimated API cost: **${total:.6f}**.",
        "Costs use returned token usage and standard paid list prices, not actual billing debits.",
        "",
        f"Frozen validation winner: **{selected or 'none'}**.",
        "",
    ]
    for split, report in reports.items():
        lines += [
            f"## {split.title()}",
            "",
            "| Policy | Accepted / cases | Cost / accepted | Total cost | p95 task latency |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in report["candidates"]:
            cps = (
                f"${row['cost_per_success']:.6f}"
                if row["cost_per_success"] is not None
                else "unavailable"
            )
            lines.append(
                f"| {NAMES[row['policy_version']]} | {row['success_count']} / {row['runs']} | "
                f"{cps} | ${row['known_total_cost']:.6f} | "
                f"{row['latency_p95_ms'] / 1000:.3f}s |"
            )
        lines += [
            "",
            f"Quality floor: {report['quality_floor']:.1%}. Selection status: {report['status']}.",
            "",
        ]
    lines += [
        "## Interpretation",
        "",
        (
            "- Compare the frozen winner with both fixed Flash and fixed Flash-Lite. "
            "Savings against Flash alone do not prove value beyond choosing a "
            "cheaper model."
        ),
        (
            "- Gates, prompts and routing rules were fixed before collection. The "
            "test split does not choose a new policy."
        ),
        (
            "- All model attempts and fallback calls are charged in the numerator. "
            "Local validation CPU time is included in task latency but not "
            "monetized."
        ),
        (
            "- Shared templates, a small sample, one provider, one run per case and "
            "sequential policy order limit generalization and latency conclusions."
        ),
        (
            "- Bootstrap intervals are descriptive. Perfect scores on this small "
            "sample cannot establish zero production error."
        ),
        f"- Raw token audit vs Reins cost ledger: {'PASS' if not mismatches else 'FAIL'}.",
        "",
        "Pricing: https://ai.google.dev/gemini-api/docs/pricing",
        "",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines))
    return result


def plot(folder, result):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = result["reports"].get("test", result["reports"]["validation"])
    rows = report["candidates"]
    labels = [NAMES[r["policy_version"]] for r in rows]
    colors = [
        "#929d90" if r["policy_version"] != result["frozen_validation_policy"] else "#29634b"
        for r in rows
    ]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.7), layout="constrained")
    specs = [
        ("Acceptance rate (%)", [100 * r["success_rate"] for r in rows]),
        ("USD per 1,000 accepted tasks", [(r["cost_per_success"] or 0) * 1000 for r in rows]),
        ("p95 task latency (seconds)", [r["latency_p95_ms"] / 1000 for r in rows]),
    ]
    for ax, (title, values) in zip(axes, specs):
        bars = ax.barh(labels, values, color=colors, height=0.58)
        ax.invert_yaxis()
        ax.set_title(title, pad=18)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.bar_label(bars, fmt="%.2f", padding=5)
        ax.set_xlim(0, max(values) * 1.24)
    fig.suptitle("Reins / Held-out API experiment", fontsize=18, fontweight="bold")
    fig.supxlabel(
        "Constructed invoices · standard list-price estimates · green = frozen validation winner",
        fontsize=9,
    )
    fig.savefig(folder / "results.png", dpi=180)
    fig.savefig(folder / "results.pdf")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    result = analyze(args.folder)
    plot(args.folder, result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("reports", "case_details")}, indent=2
        )
    )
