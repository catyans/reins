"""Post-run diagnostics only. Never changes frozen outcomes or selection."""

import html
import json
import re
from collections import defaultdict
from pathlib import Path

from project_experiment import results

from reins.projects.documents import FIELDS, atomic_json, digest, field_sources


def visible(text):
    text = html.unescape(text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    return " ".join(text.split())


def main():
    root = Path("output/benchmarks/projects-v2")
    dataset = json.loads((root / "dataset.json").read_text())
    snapshots = {
        (p["project"], s["commit"]): s for p in dataset["projects"] for s in p["snapshots"]
    }
    counts = defaultdict(
        lambda: {"completed": 0, "literal_source_failures": 0, "format_normalization_candidates": 0}
    )
    groups = defaultdict(list)
    examples = []
    for row in results(root):
        if "commit" not in row:
            continue
        stats = counts[row["policy"]]
        stats["completed"] += 1
        if not row["source_backed"]:
            stats["literal_source_failures"] += 1
            sources = field_sources(snapshots[(row["project"], row["commit"])])
            plausible = True
            for field in FIELDS:
                answer = row["fields"].get(field)
                if not isinstance(answer, dict):
                    plausible = False
                    break
                if answer.get("value") is None:
                    plausible &= answer.get("source") is None and answer.get("quote") is None
                    continue
                original = sources[field].get(answer.get("source"), "")
                quote = answer.get("quote")
                plausible &= (
                    isinstance(quote, str) and bool(quote) and visible(quote) in visible(original)
                )
                if field != "license":
                    plausible &= visible(answer["value"]) in visible(original)
            if plausible:
                stats["format_normalization_candidates"] += 1
                examples.append(
                    {
                        "project": row["project"],
                        "policy": row["policy"],
                        "phase": row["phase"],
                        "id": row["id"],
                    }
                )
        if row["judge_complete"]:
            identity = digest([row["project"], row["commit"], row["fields"]])
            groups[identity].append(
                {k: row[k] for k in ("id", "project", "policy", "phase", "field_scores")}
            )
    repeated = [g for g in groups.values() if len(g) > 1]
    disagreements = [g for g in repeated if len({digest(r["field_scores"]) for r in g}) > 1]
    audit = {
        "kind": "post-run diagnostic; no scores or policies changed",
        "format_normalization": "Heuristic whitespace/Markdown stripping; code needs manual review",
        "counts": dict(counts),
        "format_review_examples": examples,
        "identical_answer_groups": len(repeated),
        "groups_with_judge_disagreement": len(disagreements),
        "judge_disagreement_examples": disagreements,
        "caveat": "Formatting candidates need semantic review; these are not new accepted outcomes",
    }
    atomic_json(root / "quality-diagnostics.json", audit)
    atomic_json("website/project-quality-audit.json", audit)
    print(
        json.dumps(
            {
                k: audit[k]
                for k in ("counts", "identical_answer_groups", "groups_with_judge_disagreement")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
