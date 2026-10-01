"""Frozen, auditable policy bundles learned from paired validation outcomes."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from reins.checkpoints import fingerprint


def task_segment(payload):
    """Only input-side features. Never inspect expected answers or test labels."""
    text = payload.get("source", "")
    length = "long" if len(text) > 4000 else "short"
    conflict = "conflict" if payload.get("source_conflict", False) else "ordinary"
    return length + ":" + conflict


@dataclass(frozen=True)
class PolicyBundle:
    version: str
    task_type: str
    baseline: str
    configurations: dict
    segments: dict
    evaluator_version: str
    validation_hash: str
    diagnostics: dict

    def choose(self, payload):
        segment = task_segment(payload)
        candidate = self.segments.get(segment, self.baseline)
        return candidate, (
            "validated_segment" if segment in self.segments else "baseline_no_evidence"
        )

    def save(self, path):
        value = asdict(self)
        value["checksum"] = fingerprint(value)
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        tmp.replace(dest)

    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text())
        digest = value.pop("checksum")
        if fingerprint(value) != digest:
            raise ValueError("Policy bundle checksum mismatch")
        return cls(**value)


def learn_bundle(
    reports,
    configurations,
    *,
    task_type,
    baseline,
    evaluator_version,
    min_cases=30,
    min_acceptance=0.95,
    max_p95_ms=60000,
    split="validation",
):
    """Select by segment only with complete, paired, settled validation evidence.

    Each policy's rows contain id, segment, success, cost, cost_complete, total_ms.
    Acceptance must match/exceed baseline's observed acceptance; this is not a
    population-level non-inferiority proof. Ineligible or sparse segments stay on
    baseline. The caller owns label provenance and semantic split isolation.
    """
    if split != "validation":
        raise ValueError("Policy learning is only allowed on validation data")
    if baseline not in reports or set(reports) != set(configurations):
        raise ValueError("Configurations and reports must match and include baseline")
    if type(min_cases) is not int or min_cases < 1:
        raise ValueError("min_cases must be positive")
    if not math.isfinite(min_acceptance) or not 0 <= min_acceptance <= 1:
        raise ValueError("min_acceptance must be in [0,1]")
    if not math.isfinite(max_p95_ms) or max_p95_ms <= 0:
        raise ValueError("max_p95_ms must be finite and positive")
    base = {r["id"]: r for r in reports[baseline]}
    if not base or len(base) != len(reports[baseline]):
        raise ValueError("Unique nonempty validation cases required")
    for name, rows in reports.items():
        if len(rows) != len(base) or {r["id"] for r in rows} != set(base):
            raise ValueError("All policies must have the same unique cases")
        for r in rows:
            if type(r["success"]) is not bool or r["segment"] != base[r["id"]]["segment"]:
                raise ValueError("Invalid outcome or mismatched segment")
    choices, diagnostics = {}, {}
    for segment in sorted({r["segment"] for r in base.values()}):
        ref = [r for r in base.values() if r["segment"] == segment]
        floor = max(min_acceptance, sum(r["success"] for r in ref) / len(ref))
        rows_by_policy = {
            name: [r for r in rows if r["segment"] == segment] for name, rows in reports.items()
        }
        results = []

        def complete(rows):
            return all(
                r.get("cost_complete") is True
                and isinstance(r.get("cost"), (int, float))
                and math.isfinite(r["cost"])
                and r["cost"] >= 0
                for r in rows
            )

        for name, rows in rows_by_policy.items():
            reasons = []
            success = sum(r["success"] for r in rows)
            if len(rows) < min_cases:
                reasons.append("insufficient_cases")
            if success / len(rows) < floor:
                reasons.append("quality_below_baseline_or_floor")
            if not complete(rows) or not complete(ref):
                reasons.append("unsettled_cost")
            times = [r.get("total_ms") for r in rows]
            if any(not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 for t in times):
                reasons.append("invalid_timing")
            elif sorted(times)[max(0, math.ceil(0.95 * len(times)) - 1)] > max_p95_ms:
                reasons.append("latency_above_limit")
            results.append(
                {
                    "policy": name,
                    "reasons": reasons,
                    "cost_per_accepted": sum(r["cost"] for r in rows) / success
                    if not reasons and success
                    else None,
                }
            )
        eligible = [r for r in results if r["cost_per_accepted"] is not None]
        if eligible:
            choices[segment] = min(
                eligible, key=lambda r: (r["cost_per_accepted"], r["policy"] != baseline)
            )["policy"]
        diagnostics[segment] = results
    validation_hash = fingerprint(reports)
    version = fingerprint([task_type, configurations, choices, evaluator_version, validation_hash])[
        :16
    ]
    return PolicyBundle(
        version,
        task_type,
        baseline,
        configurations,
        choices,
        evaluator_version,
        validation_hash,
        diagnostics,
    )


def assess_holdout(candidate, baseline, *, min_cases=300, min_acceptance=0.95, max_p95_ms=60000):
    """Release gate, never a second selector. A failed frozen policy stays blocked."""
    if type(min_cases) is not int or min_cases < 1:
        raise ValueError("min_cases must be positive")
    if not math.isfinite(min_acceptance) or not 0 <= min_acceptance <= 1:
        raise ValueError("Invalid acceptance floor")
    if not math.isfinite(max_p95_ms) or max_p95_ms <= 0:
        raise ValueError("Invalid latency limit")
    c = {r["id"]: r for r in candidate}
    b = {r["id"]: r for r in baseline}
    if not c or set(c) != set(b) or len(c) != len(candidate) or len(b) != len(baseline):
        raise ValueError("Complete paired unique holdout cases required")
    if any(type(r["success"]) is not bool for r in candidate + baseline):
        raise ValueError("Explicit Boolean outcomes required")
    reasons = []
    if len(c) < min_cases:
        reasons.append("insufficient_holdout_cases")
    accepted = sum(r["success"] for r in candidate)
    baseline_accepted = sum(r["success"] for r in baseline)
    if accepted / len(c) < min_acceptance or accepted < baseline_accepted:
        reasons.append("quality_gate_failed")
    times = [r.get("total_ms") for r in candidate]
    if any(not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 for t in times):
        reasons.append("invalid_latency")
    elif sorted(times)[math.ceil(0.95 * len(times)) - 1] > max_p95_ms:
        reasons.append("latency_gate_failed")
    complete = all(
        r.get("cost_complete") is True
        and isinstance(r.get("cost"), (int, float))
        and math.isfinite(r["cost"])
        and r["cost"] >= 0
        for r in candidate + baseline
    )
    if not complete:
        reasons.append("incomplete_costs")
    elif accepted and baseline_accepted:
        if (
            sum(r["cost"] for r in candidate) / accepted
            >= sum(r["cost"] for r in baseline) / baseline_accepted
        ):
            reasons.append("no_cost_improvement")
    else:
        reasons.append("no_accepted_results")
    return {
        "status": "hold" if reasons else "eligible_for_manual_adoption",
        "reasons": reasons,
        "candidate_accepted": accepted,
        "baseline_accepted": baseline_accepted,
        "cases": len(c),
        "policy_reselected": False,
        "population_equivalence_claimed": False,
    }
