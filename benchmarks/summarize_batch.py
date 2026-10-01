"""Report an audit without reselecting the winner or hiding prior trials."""

import argparse
import json
from pathlib import Path


def main(folder, baseline_report=None):
    summary = json.loads((folder / "summary.json").read_text())
    selected = summary["selected"]
    rows = summary["results"]["audit"]
    extra = None
    if baseline_report:
        extra = json.loads((baseline_report / "summary.json").read_text())
        rows = rows + extra["results"]["audit"]
        reference = {r["id"] for r in rows[0]["records"]}
        if any({r["id"] for r in row["records"]} != reference for row in rows):
            raise ValueError("The added baseline must use identical audit cases")
    by_name = {r["policy"]: r for r in rows}
    winner = by_name[selected]
    comparisons = {}
    for baseline in [
        name for name in ("single-lite", "verbose-batch-4", "compact-batch-4") if name in by_name
    ]:
        row = by_name[baseline]
        comparisons[baseline] = {
            "cost_reduction": 1 - winner["cost_per_accepted"] / row["cost_per_accepted"],
            "job_throughput_multiplier": winner["documents_per_second"]
            / row["documents_per_second"],
            "acceptance_difference": winner["rate"] - row["rate"],
        }
    output = {
        "selected": selected,
        "audit_cases": winner["cases"],
        "comparisons": comparisons,
        "estimated_total_usd": summary["estimated_cost"]
        + (extra["estimated_cost"] if extra else 0),
        "calls": summary["calls"] + (extra["calls"] if extra else 0),
        "cost_audit_matches": abs(summary["estimated_cost"] - summary["ledger_cost"]) < 1e-8
        and (not extra or abs(extra["estimated_cost"] - extra["ledger_cost"]) < 1e-8),
        "provider_errors": summary["provider_errors"] + (extra["provider_errors"] if extra else 0),
        "additional_baseline": "Compact batch 4, post-hoc stress comparison; no reselection"
        if extra
        else None,
        "audit": [{k: v for k, v in r.items() if k != "records"} for r in rows],
        "scope": (
            "Real Gemini API calls on constructed invoices. "
            "Same calculator for all policies. No customer ROI claim."
        ),
    }
    (folder / "analysis.json").write_text(json.dumps(output, indent=2) + "\n")
    lines = [
        "# Reins: reducing model work without lowering acceptance",
        "",
        "Real API experiment; constructed invoices, not customer production data.",
        "",
        f"Frozen validation selection: **{selected}**. Audit: **{winner['cases']} new documents**.",
        "All policies use the same model, identity alignment and deterministic amount calculator.",
        "",
        "| Policy | Accepted | USD / 1,000 accepted | Documents / second | "
        "p95 batch service | API calls |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['policy']} | {r['accepted']}/{r['cases']} | "
            f"${r['cost_per_accepted'] * 1000:.5f} | {r['documents_per_second']:.2f} | "
            f"{r['p95_wait_seconds']:.2f}s | {r['api_calls']} |"
        )
    lines += ["", "## Comparisons", ""]
    for name, c in comparisons.items():
        lines.append(
            f"- Versus {name}: {c['cost_reduction']:.1%} lower cost per accepted invoice; "
            f"{c['job_throughput_multiplier']:.2f}× measured sequential-job throughput; "
            f"{c['acceptance_difference']:+.1%} acceptance difference."
        )
    lines += [
        "",
        "## What changed",
        "",
        "- Multiple independent documents share task instructions in one model request.",
        "- Compact arrays avoid repeating field names and unneeded evidence text. "
        "The evaluated deliverable is the same five business fields for all policies; "
        "do not use this configuration if a customer requires evidence quotes.",
        "- Results are matched by invoice identity, not output order. Missing or invalid "
        "items alone use single-item fallback.",
        "- Explicit tax-free arithmetic is computed with Decimal from source values. "
        "Every baseline receives the same calculator and response alignment.",
        "- Validation selects batch size; the audit does not select again.",
        "",
        "## Limits",
        "",
        "- These are one provider, one constructed workload and shared document templates. "
        "New IDs and amounts are disjoint, not a wholly independent distribution.",
        "- Throughput is documents completed per second of a sequential job. "
        "It is not serving throughput at fixed concurrency. Larger batches can increase "
        "individual completion latency. Queueing behind earlier batches is excluded "
        "from the batch-service p95.",
        "- Cost is returned tokens times standard paid API prices, not a billing debit. "
        "Local CPU/machine cost is not monetized. No search or paid judge was used.",
        "- A perfect 100-case audit does not prove a zero production error rate. "
        "This is an engineering result, not yet a proprietary research moat.",
        "",
        f"Total calls including the additional baseline: {output['calls']}; list-price estimate: "
        f"${output['estimated_total_usd']:.6f}. Ledger agreement: {output['cost_audit_matches']}.",
        output["additional_baseline"] or "No additional post-hoc baseline.",
        "",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines))
    plot(folder, rows, selected)
    print(json.dumps(output, indent=2))


def plot(folder, rows, selected):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = {
        "single-lite": "Single Lite + calculator",
        "verbose-batch-4": "Batch 4 + calculator",
        "compact-batch-4": "Compact 4 + calculator",
        "compact-batch-8": "Compact 8 + calculator",
        "compact-batch-16": "Compact 16 + calculator",
    }
    colors = ["#285f47" if r["policy"] == selected else "#9da99c" for r in rows]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4), layout="constrained")
    series = [
        ("USD / 1,000 accepted invoices", [r["cost_per_accepted"] * 1000 for r in rows]),
        ("Sequential job: documents / second", [r["documents_per_second"] for r in rows]),
        ("p95 batch service time (seconds)", [r["p95_wait_seconds"] for r in rows]),
    ]
    for ax, (title, values) in zip(axes, series):
        bars = ax.barh([names[r["policy"]] for r in rows], values, color=colors, height=0.56)
        ax.invert_yaxis()
        ax.set_title(title, fontsize=11, pad=18)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.bar_label(bars, fmt="%.3f", padding=5)
        ax.set_xlim(0, max(values) * 1.35)
    fig.suptitle(
        "Reins / Held-out audit of the frozen batch policy", fontsize=17, fontweight="bold"
    )
    quality = " · ".join(f"{names[r['policy']]}: {r['accepted']}/{r['cases']}" for r in rows)
    fig.supxlabel(
        quality + "\nReal API calls · constructed documents · standard list-price estimates",
        fontsize=9,
    )
    fig.savefig(folder / "results.png", dpi=180)
    fig.savefig(folder / "results.pdf")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()
    main(args.folder, args.baseline_report)
