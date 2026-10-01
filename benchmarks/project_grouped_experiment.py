"""Finite regression comparison on an existing dataset; never publish automatically."""

import argparse
import asyncio
import html
import json
import shutil
from pathlib import Path

from project_experiment import evaluate, results, summarize

from reins import configure
from reins.core.decorators import shutdown
from reins.projects.documents import atomic_json, digest
from reins.projects.runtime import Gemini, Runner, State, load_key

POLICIES = ("fixed-flash", "cached-flash", "grouped-incremental")


def recommend(report):
    scores = report["strategies"]
    baseline = scores["fixed-flash"]
    # Missing baseline judgments count as possible successes, not failures, for
    # this conservative regression recommendation. This is NOT a production gate.
    possible_baseline = baseline["accepted"] + baseline["tasks"] - baseline["assessed"]
    eligible = {
        p: v
        for p, v in scores.items()
        if p != "fixed-flash"
        and v["cost_complete"]
        and v["accepted"] >= possible_baseline
        and v["estimated_cost"] < baseline["estimated_cost"]
    }
    chosen = min(eligible, key=lambda p: eligible[p]["cost_per_accepted"]) if eligible else None
    return {
        "recommended_for_regression": chosen,
        "minimum_total_cost_saving_vs_fixed_flash": (
            1 - eligible[chosen]["estimated_cost"] / baseline["estimated_cost"] if chosen else None
        ),
        "baseline_accepted_upper_bound": possible_baseline,
        "selection": "Retrospective choice on the existing dataset; not fresh validation",
        "production_gate_passed": False,
        "reason": "Absolute acceptance below 95%; incomplete evaluations; small historical replay",
    }


def render_report(root, report):
    names = {
        "fixed-flash": "Flash full refresh",
        "cached-flash": "Flash + document cache",
        "grouped-incremental": "Reins grouped updates",
    }
    scores = report["strategies"]
    maximum = max(s["cost_upper_bound"] for s in scores.values()) or 1
    blocks = []
    for policy, score in scores.items():
        cost, upper = score["estimated_cost"], score["cost_upper_bound"]
        accepted = score["accepted"]
        unit = (
            f"${cost / accepted:.5f}–${upper / accepted:.5f}" if accepted else "No accepted results"
        )
        blocks.append(
            f"<article><h2>{html.escape(names[policy])}</h2>"
            f"<b>{accepted}/{score['tasks']} accepted</b>"
            f"<p>{score['assessed']} evaluated · {score['calls']} model calls</p>"
            f"<div class='track'><div style='width:{100 * upper / maximum:.2f}%'></div></div>"
            f"<p>Total API cost: ${cost:.4f}–${upper:.4f}</p>"
            f"<p>Cost per observed accepted result: {unit}</p></article>"
        )
    body = (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reins grouped updates — measured comparison</title>
<style>body{font:17px/1.6 system-ui;
background:#f6f7f0;
color:#19382c;
max-width:1000px;
margin:40px auto;
padding:24px}

h1{font-size:40px;
line-height:1.15}
section{display:grid;
grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
gap:18px}

article{background:white;
border:1px solid #d8e0d2;
border-radius:16px;
padding:22px}
h2{font-size:20px}
b{font-size:25px}

.track{height:16px;
background:#edf1e9;
border-radius:8px;
overflow:hidden}
.track div{height:100%;
background:#629944}

small{color:#5c6c61}a{color:inherit}</style>
<h1>Fewer requests. Quality measured alongside cost.</h1>
<p>20 public projects · Three real release snapshots + one unchanged recheck · Same Gemini
Flash model</p>
<section>"""
        + "".join(blocks)
        + """</section>
<p>Quality requires exact source support and the unchanged independent model-assisted evaluator.
This is a regression on an existing dataset, not new unseen validation or a
customer-production result.</p>
<p>Cost ranges retain the full reserved upper bound for unresolved requests. Missing
evaluations remain in
the denominator. Cost per observed accepted result does not predict acceptance of unevaluated tasks.
API estimates include extraction and exclude separate research evaluation; they are not
invoice totals.</p>
<p>Ordinary document caching is an explicit control. No claim of statistical quality equivalence or
production readiness follows from this small run.</p><a href="report.json">Full measured
data</a></html>"""
    )
    choice = report.get("recommendation", {}).get("recommended_for_regression")
    if choice:
        saving = report["recommendation"]["minimum_total_cost_saving_vs_fixed_flash"]
        note = (
            f"<p><strong>Current regression recommendation: {names[choice]}. "
            f"At least {saving:.1%} lower workload API cost than full refresh.</strong> "
            "Selected after comparing these results; not a production quality certification. "
            "The more complex grouped candidate did not beat ordinary caching on cost per "
            "accepted result.</p>"
        )
        body = body.replace("<section>", note + "<section>", 1)
    (root / "report.html").write_text(body)


async def main(args):
    source, root = Path(args.source), Path(args.output)
    data = json.loads((source / "dataset.json").read_text())
    data["projects"] = [p for p in data["projects"] if p["split"] == args.split]
    data["dataset_hash"] = digest(data["projects"])
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "dataset.json", data)
    protocol = {
        "policies": POLICIES,
        "dataset_hash": data["dataset_hash"],
        "quality": "Original strict source support plus unchanged independent judge",
        "scope": "Regression on previously examined dataset, not a new unseen test",
        "decision": "Report every arm; no automatic publication or promotion",
        "extraction_concurrency": 2,
        "evaluation_concurrency": 1,
        "runtime_hash": digest(Path("src/reins/projects/runtime.py").read_text()),
    }
    if (root / "protocol.json").exists():
        assert json.loads((root / "protocol.json").read_text()) == protocol
    atomic_json(root / "protocol.json", protocol)
    shutil.copytree(source / "references", root / "references", dirs_exist_ok=True)
    key = load_key(args.key_file)
    configure(storage_path=str(root / "execution" / "traces.duckdb"))
    state = State(root / "execution")
    caller = Gemini(state, key, limit=15)
    caller.semaphore = asyncio.Semaphore(2)
    runner = Runner(state, caller, root / "status.json")
    try:
        await runner.run(data, policies=POLICIES)
    finally:
        runner.close()
        state.close()
        shutdown()
    await evaluate(data, root, key, (args.split,))
    rows = results(root)
    report = {
        "protocol": protocol,
        "projects": len(data["projects"]),
        "strategies": {p: summarize([r for r in rows if r["policy"] == p]) for p in POLICIES},
        "phases": {
            phase: {
                p: summarize([r for r in rows if r["policy"] == p and r["phase"] == phase])
                for p in POLICIES
            }
            for phase in ("initial", "update-1", "update-2", "unchanged")
        },
    }
    report["recommendation"] = recommend(report)
    atomic_json(root / "report.json", report)
    render_report(root, report)
    print(json.dumps(report["strategies"], indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="output/benchmarks/projects-v2")
    p.add_argument("--output", default="output/benchmarks/projects-grouped-v1")
    p.add_argument("--split", default="test")
    p.add_argument("--key-file", required=True)
    asyncio.run(main(p.parse_args()))
