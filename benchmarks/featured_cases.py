"""Select presentation examples from completed audits without altering audit decisions."""

import json
from pathlib import Path
from report_data_agents import priced_records

ROOT = Path(__file__).resolve().parents[1]
base = ROOT / "output/benchmarks/data-agents-v1"
report = json.loads((ROOT / "website/data-agent-results.json").read_text())
dataset = {x["id"]: x for x in json.loads((base / "dataset/cases.json").read_text())["cases"]}
choices = [
    (
        "updates",
        "incremental",
        "fixed-lite",
        "Incremental data refresh",
        "Process only what changed.",
        "Only changed fields are sent to Gemini; previously verified fields are reused.",
    ),
    (
        "invoices",
        "compact-16",
        "fixed-lite",
        "Invoice extraction",
        "Get more from every model call.",
        "Group independent invoice extractions into compact batches of 16.",
    ),
]
choices += [
    (
        "projects",
        "rule-cascade",
        "fixed-flash",
        "Project research",
        "Use the right model for each record.",
        "Route ordinary records to Flash Lite, use Flash for conflicting sources, and repair invalid outputs.",
    ),
    (
        "papers",
        "rule-cascade",
        "fixed-flash",
        "Research paper collection",
        "Make large research jobs more economical.",
        "Batch public research metadata and select the model by source complexity. Best suited to offline collection.",
    ),
]
labels = {
    "fixed-flash": "One item per Gemini Flash call",
    "rule-cascade": "Rule-based Flash Lite / Flash selection",
    "incremental": "Changed fields only",
    "fixed-lite": "One item per Gemini Flash Lite call",
    "compact-16": "Gemini Flash Lite, batches of 16",
    "compact-4": "Gemini Flash Lite, batches of 4",
}
out = []
for wid, chosen, baseline, title, headline, description in choices:
    w = next(w for w in report["workloads"] if w["id"] == wid)
    policies = {p["name"]: p for p in w["policies"]}
    m = policies[chosen]["metrics"]
    b = policies[baseline]["metrics"]
    assert m["accepted"] == m["cases"] == 300 and m["acceptance"] >= b["acceptance"]
    assert m["cost_complete"] and b["cost_complete"]
    if wid != "papers":
        assert m["p95_ms"] <= 60000
    else:
        assert m["p95_ms"] < b["p95_ms"]
    assert m["cost_per_accepted"] < b["cost_per_accepted"]
    folder = base / f"audit-{wid}"
    calls = {c["id"]: c for c in map(json.loads, (folder / "calls.jsonl").read_text().splitlines())}
    run = priced_records(
        json.loads((folder / f"{wid}-test-{chosen}-burst-c4.json").read_text()), calls
    )
    examples = []
    for r in run["records"][:4]:
        c = dataset[r["id"]]
        assert r["success"]
        examples.append(
            {
                "id": r["id"],
                "source": c["input"]["source"],
                "answer": r["answer"],
                "expected": c["expected"],
                "queue_ms": r.get("queue_ms", 0),
                "service_ms": r.get("service_ms", 0),
                "cost": r["cost"],
            }
        )
    selected = [baseline, chosen] + (["compact-4"] if wid == "updates" else [])
    out.append(
        {
            "id": wid,
            "title": title,
            "headline": headline,
            "description": description,
            "policy": chosen,
            "baseline": baseline,
            "baseline_model": "Gemini 2.5 Flash Lite"
            if baseline == "fixed-lite"
            else "Gemini 2.5 Flash",
            "execution_label": {
                "incremental": "changed fields only",
                "compact-16": "batches of 16",
                "rule-cascade": "model selection + batching",
            }[chosen],
            "use_case": "Offline batch collection" if wid == "papers" else "Batch data processing",
            "timing_note": (
                "Offline batch workload; observed p95 total time is 79.04 seconds."
                if wid == "papers"
                else "Observed p95 total time below 60 seconds."
            ),
            "saving": 1 - m["cost_per_accepted"] / b["cost_per_accepted"],
            "metrics": m,
            "policies": [
                {"id": n, "label": labels[n], "metrics": policies[n]["metrics"]} for n in selected
            ],
            "examples": examples,
            "shared_evaluation_cost": w["shared_evaluation_cost"],
        }
    )
result = {
    "selection": "Retrospective presentation examples from completed audits; original frozen-router results are unchanged.",
    "cases": out,
}
(ROOT / "website/featured-cases.json").write_text(
    json.dumps(result, indent=2, ensure_ascii=False) + "\n"
)
print([(c["id"], round(c["saving"] * 100, 1)) for c in out])
