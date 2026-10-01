"""Export measured experiment replays, paired uncertainty and honest comparisons."""

import argparse
import csv
import json
import random
import statistics
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from reins.google_usage import GoogleTextPrices, IncompleteGoogleUsage, estimate_google_text_cost
from reins.policies import assess_holdout


def priced_records(run, calls):
    """Immutable analysis overlay; original runtime ledger and frozen policies stay unchanged."""
    counts = Counter(cid for row in run["records"] for cid in row.get("calls", []))
    prices = {
        "gemini-2.5-flash-lite": GoogleTextPrices(Decimal(".1"), Decimal(".4"), Decimal(".01")),
        "gemini-2.5-flash": GoogleTextPrices(Decimal(".3"), Decimal("2.5"), Decimal(".03")),
    }
    rows = []
    for row in run["records"]:
        r = dict(row)
        if r.get("calls"):
            total = Decimal(0)
            complete = True
            for cid in r["calls"]:
                call = calls[cid]
                try:
                    total += (
                        estimate_google_text_cost(call.get("usage"), prices[call["model"]])
                        / counts[cid]
                    )
                except (IncompleteGoogleUsage, KeyError):
                    complete = False
            r.update(cost=float(total), cost_complete=complete)
        rows.append(r)
    from data_agent_live import summary

    metrics = summary(rows)
    metrics["delivered_per_second"] = len(rows) / run["wall_seconds"]
    return run | {
        "records": rows,
        "summary": metrics,
        "cost_note": (
            "Usage-based text estimate with cached-read prices; original ledger is unchanged."
        ),
    }


def paired(a, b):
    aa = {r["id"]: r for r in a}
    bb = {r["id"]: r for r in b}
    if set(aa) != set(bb):
        return {"comparable": False, "reason": "unmatched cases"}
    ids = sorted(aa)
    deltas = [int(aa[k]["success"]) - int(bb[k]["success"]) for k in ids]
    rng = random.Random(808)
    samples = []
    for _ in range(2000):
        samples.append(sum(rng.choice(deltas) for _ in ids) / len(ids))
    samples.sort()
    return {
        "comparable": True,
        "acceptance_difference": statistics.mean(deltas),
        "paired_bootstrap_95": [samples[49], samples[1949]],
        "candidate_only_pass": sum(aa[k]["success"] and not bb[k]["success"] for k in ids),
        "baseline_only_pass": sum(bb[k]["success"] and not aa[k]["success"] for k in ids),
        "note": (
            "Descriptive paired bootstrap; degenerate intervals at identical outcomes "
            "do not establish population equivalence."
        ),
    }


def main(a):
    root = Path(a.root)
    dest = Path(a.website)
    dest.mkdir(parents=True, exist_ok=True)
    dataset = json.loads((root / "dataset/cases.json").read_text())
    cases = {c["id"]: c for c in dataset["cases"]}
    workloads = []
    csv_rows = []
    for workload in ["invoices", "projects", "papers", "updates"]:
        folder = root / f"audit-{workload}"
        frozen = folder / f"{workload}-frozen.json"
        routed = folder / f"{workload}-reins.json"
        if not frozen.exists() or not routed.exists():
            continue
        freeze = json.loads(frozen.read_text())
        reins = json.loads(routed.read_text())
        policies = []
        by_name = {}
        calls = {
            r["id"]: r
            for r in (json.loads(x) for x in (folder / "calls.jsonl").read_text().splitlines())
        }
        reins = priced_records(reins, calls)
        for name, config in freeze["configurations"].items():
            file = folder / f"{workload}-test-{name}-burst-c4.json"
            if not file.exists():
                continue
            run = priced_records(json.loads(file.read_text()), calls)
            by_name[name] = run
            overhead = 0.0
            overhead_complete = True
            if name.startswith("reflector-"):
                ref = json.loads(
                    (
                        root
                        / "reflectors-v4"
                        / f"{workload}-{name.removeprefix('reflector-')}.json"
                    ).read_text()
                )
                overhead = ref["known_cost"]
                overhead_complete = ref["cost_complete"]
            metrics = run["summary"] | {
                "optimizer_cost": overhead,
                "optimizer_cost_complete": overhead_complete,
            }
            policies.append({"name": name, "config": config, "metrics": metrics})
            csv_rows.append(
                {
                    "workload": workload,
                    "policy": name,
                    **{k: v for k, v in metrics.items() if not isinstance(v, (dict, list))},
                }
            )
        validation_cost = 0.0
        development_cost = 0.0
        common_complete = True
        for f in folder.glob(f"{workload}-validation-*-burst-c4.json"):
            validation_metrics = priced_records(json.loads(f.read_text()), calls)["summary"]
            validation_cost += validation_metrics["cost"]
            common_complete &= validation_metrics["cost_complete"]
        dev_file = root / "development" / f"{workload}-development-fixed-lite-burst-c4.json"
        if dev_file.exists():
            dev_calls = {
                r["id"]: r
                for r in (
                    json.loads(line)
                    for line in (root / "development" / "calls.jsonl").read_text().splitlines()
                )
            }
            dev_metrics = priced_records(json.loads(dev_file.read_text()), dev_calls)["summary"]
            development_cost = dev_metrics["cost"]
            common_complete &= dev_metrics["cost_complete"]
        # Shared evaluation acquisition cost is separate from algorithm-specific reflection.
        common_overhead = validation_cost + development_cost
        bundle = json.loads((folder / f"{workload}-bundle.json").read_text())
        used = set(bundle["segments"].values()) | {bundle["baseline"]}
        routing_overhead = sum(
            p["metrics"].get("optimizer_cost", 0) for p in policies if p["name"] in used
        )
        routing_complete = all(
            p["metrics"].get("optimizer_cost_complete", True) for p in policies if p["name"] in used
        )
        policies.append(
            {
                "name": "reins-frozen",
                "config": {"bundle": freeze["bundle_version"]},
                "metrics": reins["summary"]
                | {
                    "optimizer_cost": routing_overhead,
                    "optimizer_cost_complete": routing_complete,
                    "shared_evaluation_cost": common_overhead,
                },
            }
        )
        csv_rows.append({"workload": workload, "policy": "reins-frozen", **policies[-1]["metrics"]})
        comparator = by_name[freeze["comparator"]]
        comparison = paired(reins["records"], comparator["records"])
        release_gate = assess_holdout(reins["records"], comparator["records"])
        b = comparator["summary"]["cost_per_accepted"]
        r = reins["summary"]["cost_per_accepted"]
        comparison["cost_reduction"] = 1 - r / b if b and r is not None else None
        comparison["cost_comparison_note"] = (
            "Zero-cost baseline: percentage savings are undefined." if b == 0 else None
        )
        comparison["target_30_percent_met"] = bool(
            comparison["cost_reduction"] is not None
            and comparison["cost_reduction"] >= 0.3
            and comparison["acceptance_difference"] >= 0
        )
        ordered = sorted(reins["records"], key=lambda x: (x["success"], x.get("decision", "")))
        examples = []
        seen = set()
        for row in ordered:
            kind = (row["success"], row.get("decision", "unknown"))
            if kind in seen:
                continue
            seen.add(kind)
            case = cases[row["id"]]
            examples.append(
                {
                    "id": row["id"],
                    "input": case["input"],
                    "expected": case["expected"],
                    "provenance": case["provenance"],
                    "output": row.get("answer"),
                    "accepted": row["success"],
                    "decision": row.get("decision", "failed"),
                    "cost": row["cost"],
                    "cost_complete": row["cost_complete"],
                    "queue_ms": row.get("queue_ms", 0),
                    "service_ms": row.get("service_ms", 0),
                    "total_ms": row["total_ms"],
                    "batch_items": row.get("batch_items", 1),
                    "calls": row.get("calls", []),
                }
            )
            if len(examples) >= 6:
                break
        reflectors = []
        for kind in ["single", "multi"]:
            p = root / "reflectors-v4" / f"{workload}-{kind}.json"
            if p.exists():
                d = json.loads(p.read_text())
                reflectors.append(
                    {
                        k: d.get(k)
                        for k in [
                            "kind",
                            "status",
                            "reason",
                            "error",
                            "model_calls",
                            "known_cost",
                            "cost_complete",
                            "wall_seconds",
                            "scope",
                            "adaptations",
                            "upstream_commit",
                        ]
                    }
                )
        stress = []
        for p in sorted(folder.glob(f"{workload}-stress-*.json")):
            if p.name.endswith(".protocol.json"):
                continue
            d = priced_records(json.loads(p.read_text()), calls)
            stress.append({"name": p.stem, "metrics": d["summary"]})
        amortized = []
        for p in policies:
            m = p["metrics"]
            unit = m.get("cost_per_accepted")
            overhead = m.get("optimizer_cost", 0) + common_overhead
            for volume in [1000, 10000, 100000]:
                amortized.append(
                    {
                        "policy": p["name"],
                        "volume": volume,
                        "usd_per_accepted": unit + overhead / volume
                        if unit is not None
                        and common_complete
                        and m.get("optimizer_cost_complete", True)
                        else None,
                    }
                )
        workloads.append(
            {
                "id": workload,
                "policies": policies,
                "comparator": freeze["comparator"],
                "comparison": comparison,
                "release_gate": release_gate,
                "examples": examples,
                "reflectors": reflectors,
                "stress": stress,
                "amortized": amortized,
                "shared_evaluation_cost": common_overhead,
                "shared_evaluation_cost_complete": common_complete,
                "bundle": json.loads((folder / f"{workload}-bundle.json").read_text()),
            }
        )
    history = []
    for folder in root.glob("reflectors*"):
        for p in folder.glob("*.json"):
            d = json.loads(p.read_text())
            history.append(
                {
                    "round": folder.name,
                    "workload": d["workload"],
                    "kind": d["kind"],
                    "status": d["status"],
                    "known_cost": d.get("known_cost", 0),
                    "cost_complete": d.get("cost_complete", False),
                }
            )
    report = {
        "version": 1,
        "generated_at": time.time(),
        "workloads": workloads,
        "dataset_cases": len(cases),
        "limitations": dataset["limitations"]
        + [
            "Gemini list-price estimates, not invoice debits. CPU cost is not monetized.",
            (
                "No AWS managed service was called. Strands results use an adapted Gemini "
                "implementation."
            ),
            (
                "Analysis prices known cached-read tokens explicitly. Original runtime "
                "pending ledger entries are retained; no bill is marked reconciled."
            ),
            (
                "Different methods ran in randomized sequential order; time-of-day provider "
                "effects remain possible."
            ),
            (
                "Independent workload audits overlapped on the same provider account. Latency"
                " is observational, not a controlled causal speedup claim."
            ),
            (
                "Traffic stress tests compare parser, fixed-lite and compact-4 policies; "
                "frozen routing is evaluated in the main burst test only."
            ),
        ],
        "reflection_history": history,
        "measured": True,
        "paid_web_requests": False,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    (dest / "data-agent-results.json").write_text(text)
    (root / "results.json").write_text(text)
    if csv_rows:
        keys = sorted(set().union(*(r.keys() for r in csv_rows)))
        with (root / "results.csv").open("w") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(csv_rows)
    lines = [
        "# Reins data-agent experiments",
        "",
        (
            "Measured Gemini API results. Public metadata is reformatted; updates and "
            "invoices include constructed inputs. No customer ROI or managed AgentCore "
            "result is claimed."
        ),
        "",
    ]
    for w in workloads:
        lines += [
            f"## {w['id'].title()}",
            "",
            "| Workload | Policy | Accepted | USD / 1,000 accepted | p95 total seconds |",
            "|---|---|---:|---:|---:|",
        ]
        for p in w["policies"]:
            m = p["metrics"]
            cost = m.get("cost_per_accepted")
            cost = "unsettled" if cost is None else f"{cost * 1000:.5f}"
            lines.append(
                f"| {w['id']} | {p['name']} | {m['accepted']}/{m['cases']} | "
                f"{cost} | {m['p95_ms'] / 1000:.2f} |"
            )
        lines += [
            "",
            f"Frozen comparator for {w['id']}: {w['comparator']}. "
            f"Target achieved: {w['comparison']['target_30_percent_met']}.",
            f"Adoption gate: {w['release_gate']['status']}; "
            f"reasons: {w['release_gate']['reasons']}.",
            "",
        ]
    lines += [
        "",
        "## Interpretation",
        "",
        (
            "A deterministic source parser is deliberately included. If it wins, the "
            "result supports avoiding unnecessary model calls; it does not establish a "
            "proprietary AI moat. Sparse segments retain the quality baseline and can "
            "cost more than a competent global rule. Failed reflection integrations do "
            "not demonstrate inferior AWS quality."
        ),
        "",
        (
            "Optimization and shared evaluation costs are separately reported and "
            "amortized at 1k/10k/100k accepted results. Historical failed development "
            "rounds are retained in reflection_history; these are not silently erased."
        ),
        "",
        "## Sources",
        "",
        "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization.html",
        "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html",
        "https://github.com/strands-labs/harness-optimizer",
        "https://ai.google.dev/gemini-api/docs/pricing",
    ]
    (root / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps({"workloads": len(workloads), "website": str(dest / "data-agent-results.json")})
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--website", required=True)
    main(p.parse_args())
