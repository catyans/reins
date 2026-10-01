"""Offline, post-hoc contract calibration and scenario discovery. No API calls."""

import json
from collections import Counter
from pathlib import Path

from project_experiment import results

from reins.projects.documents import FIELDS, atomic_json, digest, field_sources
from reins.projects.quality import SUMMARY_CONTRACT, assess


def render(root, report):
    names = {
        "fixed-flash": "Flash full refresh",
        "cached-flash": "Flash + document cache",
        "grouped-incremental": "Reins grouped updates",
    }
    table = "".join(
        f"<tr><td>{names[p]}</td><td>{v['original_accepted']}/80</td>"
        f"<td>{v['summary_accepted']}/80</td><td>{v['assessed']}/80</td></tr>"
        for p, v in report["strategies"].items()
    )
    body = (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reins — quality calibration and next workloads</title>
<style>body{font:17px/1.6 system-ui;background:#f6f7f0;color:#19382c;
max-width:950px;margin:40px auto;padding:24px}h1{font-size:36px;line-height:1.15}
section{background:white;border:1px solid #d8e0d2;border-radius:16px;padding:24px;
margin:24px 0}table{width:100%;border-collapse:collapse}td,th{text-align:left;
padding:12px;border-bottom:1px solid #d8e0d2}a{color:inherit}
.scroll{overflow-x:auto}small{color:#56665a}</style>
<h1>Match the quality check to the deliverable.</h1>
<p>Offline review of existing API outputs. No new model calls. Original outcomes preserved.</p>
<section><h2>What changed</h2>
<p>Project descriptions and release notes can be source-backed summaries.
Their source quotes must still match the document; prose whitespace can differ.
An independent factual judgment is required. Commands and license fields keep strict checks.</p>
<p>The 95% production threshold stays unchanged. This is a new task contract,
not improved model performance or a fresh held-out validation.</p></section>
<section><h2>All strategies, under both contracts</h2><div class="scroll"><table>
<tr><th>Strategy</th><th>Verbatim</th><th>Summary</th><th>Evaluated</th></tr>"""
        + table
        + """
</table></div><p>All 80 planned tasks remain in every denominator.
Missing judgments are not counted as accepted. None of the complete workflows reaches 95%.</p>
<p>Changing the contract also improves the baselines. It does not establish superior
quality or equal-quality savings for the grouped candidate.</p></section>
<section><h2>Two narrower workloads to validate next</h2>
<h3>1. Installation metadata collection</h3>
<p>77/80 installation fields from the grouped arm passed the original strict check (96.25%).
Useful for a developer tooling catalog or setup instructions database.
This is field-level evidence; standalone cost and end-to-end task quality are not yet measured.</p>
<h3>2. License-file inventory and change alerts</h3>
<p>License files are present in 78/80 snapshots. In 50/60 follow-up updates,
the license-file hash did not change. Keep the last source-backed record
and inspect changed files.</p>
<p>83.3% unchanged updates describes source stability, not measured workload cost savings.
Missing documents need review. Ordinary file caching is a baseline,
not an exclusive capability.</p></section>
<p><a href="quality-calibration.json">Auditable judgments and source links</a> ·
<a href="report.html">Original cost comparison</a></p></html>"""
    )
    (root / "quality-calibration.html").write_text(body)


def main():
    root = Path("output/benchmarks/projects-grouped-v1")
    dataset = json.loads((root / "dataset.json").read_text())
    snaps = {(p["project"], s["commit"]): s for p in dataset["projects"] for s in p["snapshots"]}
    rows = results(root)
    by_policy, examples, reasons = {}, [], Counter()
    for row in rows:
        stats = by_policy.setdefault(
            row["policy"],
            {
                "planned_tasks": 0,
                "assessed": 0,
                "original_accepted": 0,
                "summary_accepted": 0,
                "strict_fields": dict.fromkeys(FIELDS, 0),
                "summary_fields": dict.fromkeys(FIELDS, 0),
                "changed_judgments": 0,
            },
        )
        stats["planned_tasks"] += 1
        stats["original_accepted"] += int(row["accepted"])
        if not row["judge_complete"]:
            continue
        stats["assessed"] += 1
        views = field_sources(snaps[(row["project"], row["commit"])])
        old = assess(row["fields"], views, row["field_scores"])
        new = assess(row["fields"], views, row["field_scores"], contract=SUMMARY_CONTRACT)
        assert old["accepted"] == row["accepted"]
        stats["summary_accepted"] += int(new["accepted"])
        for field in FIELDS:
            stats["strict_fields"][field] += int(old["fields"][field])
            stats["summary_fields"][field] += int(new["fields"][field])
        if old["accepted"] != new["accepted"]:
            stats["changed_judgments"] += 1
            examples.append(
                {
                    "id": row["id"],
                    "project": row["project"],
                    "policy": row["policy"],
                    "phase": row["phase"],
                    "fields": row["fields"],
                    "semantic_scores": row["field_scores"],
                    "sources": row["sources"],
                }
            )
        if not new["accepted"]:
            reasons[row.get("reason", "No complete judgment")] += 1
    present, unchanged, transitions = 0, 0, 0
    for project in dataset["projects"]:
        previous = None
        for i, snap in enumerate(project["snapshots"] + [project["snapshots"][-1]]):
            license_doc = snap["documents"].get("license")
            current = license_doc["hash"] if license_doc else None
            present += int(current is not None)
            if i:
                transitions += 1
                unchanged += int(current is not None and current == previous)
            previous = current
    report = {
        "kind": "Post-hoc task-contract calibration; not improved model performance",
        "dataset_hash": dataset["dataset_hash"],
        "contract": SUMMARY_CONTRACT,
        "rules": {
            "purpose_release_note": (
                "Source-backed summaries with semantic approval; "
                "whitespace-only quote normalization"
            ),
            "install_command_license": "Original strict validation",
            "missing_evaluation": "Unaccepted and retained in denominator",
            "production_threshold": 0.95,
        },
        "costs": "Reuse existing requests and judgments; no new API calls",
        "strategies": by_policy,
        "examples": examples,
        "remaining_failure_reasons": dict(reasons),
        "scenario_discovery": {
            "license_inventory": {
                "available_license_documents": present,
                "planned_tasks": 80,
                "unchanged_license_updates": unchanged,
                "all_update_tasks": transitions,
                "interpretation": (
                    "Source stability suggests reuse opportunity; "
                    "not measured standalone cost savings"
                ),
            },
            "installation_metadata": {
                "interpretation": (
                    "Use strict_fields.install_command counts. "
                    "Standalone costs and end-to-end execution not measured"
                )
            },
            "project_summary": {
                "interpretation": (
                    "New summary contract is a different deliverable from verbatim extraction; "
                    "original outcomes retained"
                )
            },
        },
        "fresh_validation_required": True,
        "automatic_promotion": False,
        "implementation_hash": digest(Path("src/reins/projects/quality.py").read_text()),
    }
    atomic_json(root / "quality-calibration.json", report)
    render(root, report)
    print(json.dumps({k: report[k] for k in ("strategies", "scenario_discovery")}, indent=2))


if __name__ == "__main__":
    main()
