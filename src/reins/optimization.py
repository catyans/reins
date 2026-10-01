"""Quality-constrained, paired policy experiments; no model calls or auto-deployment.

Candidate functions own their provider/model configuration. Reins measures their
instrumented calls and caller-reported costs. This is empirical policy selection,
not a learned router or a statistical guarantee of future task quality.
"""

from __future__ import annotations

import inspect
import json
import math
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from uuid import uuid4

from reins.core.context import get_current_run
from reins.core.outcomes import compare_tasks
from reins.evaluation import evaluate_dataset


@dataclass(frozen=True)
class Constraints:
    min_success_rate: float = 0.95
    max_quality_drop: float = 0.0
    min_cases: int = 30
    max_p95_ms: float | None = None

    def __post_init__(self):
        for value in (self.min_success_rate, self.max_quality_drop):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Quality constraints must be finite and between zero and one")
        if type(self.min_cases) is not int or self.min_cases < 1:
            raise ValueError("min_cases must be a positive integer")
        if self.max_p95_ms is not None and (
            not math.isfinite(self.max_p95_ms) or self.max_p95_ms <= 0
        ):
            raise ValueError("max_p95_ms must be finite and positive")


async def evaluate_experiment(
    cases,
    candidates,
    *,
    baseline,
    constraints=None,
    task_type="field_extraction",
    budget=None,
    mode=None,
    split="validation",
    evaluator=None,
    evaluator_version="exact-fields-v1",
):
    """Evaluate every candidate on identical cases, with one observation per case.

    Use versioned candidate names. The agent receives only a deep copy of input;
    the evaluator alone receives expected answers. Validation selects a policy;
    test runs report results without selecting a new winner. Budget rejection
    must not silently swap the candidate being measured.
    """
    from reins.core.decorators import _get_runtime

    if get_current_run() is not None:
        raise ValueError("Start experiments outside a traced task")
    constraints = constraints or Constraints()
    if split not in {"validation", "test"}:
        raise ValueError("split must be validation or test")
    if baseline not in candidates or len(candidates) < 2:
        raise ValueError("Supply a baseline and at least one other candidate")
    if any(not isinstance(k, str) or not k or not callable(v) for k, v in candidates.items()):
        raise ValueError("Candidates must map versioned names to callables")
    if not cases or any(not isinstance(c.get("id"), str) or not c["id"] for c in cases):
        raise ValueError("Use nonempty string case IDs")
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    if any(not isinstance(c.get("expected"), dict) or not c["expected"] for c in cases):
        raise ValueError("Each case needs nonempty expected fields")
    if not evaluator_version or (evaluator is not None and evaluator_version == "exact-fields-v1"):
        raise ValueError("Give custom evaluators an explicit version")
    experiment_id = str(uuid4())
    runtime = _get_runtime()
    # Manifest is persisted separately from runs so an interrupted candidate
    # cannot silently disappear from the declared experiment.
    runtime.storage.query(
        """CREATE TABLE IF NOT EXISTS optimization_experiments
        (experiment_id VARCHAR PRIMARY KEY, manifest VARCHAR)"""
    )
    manifest = dict(
        baseline=baseline,
        candidates=list(candidates),
        cases=[c["id"] for c in cases],
        task_type=task_type,
        split=split,
        evaluator_version=evaluator_version,
        constraints=asdict(constraints),
    )
    runtime.storage.query(
        "INSERT INTO optimization_experiments VALUES (?, ?)",
        [experiment_id, json.dumps(manifest)],
    )
    for name, agent in candidates.items():
        await evaluate_dataset(
            cases,
            agent,
            policy_version=name,
            budget=budget,
            task_type=task_type,
            mode=mode,
            experiment_id=experiment_id,
            split=split,
            evaluator=evaluator,
            evaluator_version=evaluator_version,
            on_exceed="reject",
        )
    return recommend(runtime.storage, experiment_id, constraints=constraints)


def list_experiments(storage):
    tables = storage.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name='optimization_experiments'"
    )
    if not tables:
        return []
    return [
        {"experiment_id": row["experiment_id"], **json.loads(row["manifest"])}
        for row in storage.query("SELECT * FROM optimization_experiments ORDER BY experiment_id")
    ]


def recommend(storage, experiment_id, *, constraints=None):
    """Select the cheapest eligible policy only within a complete paired experiment.

    A test split intentionally has no recommendation. Incomplete/duplicate cases,
    pending charges, and mismatched cohorts block selection. Confidence intervals
    describe each rate; they are not used as proof of statistical non-inferiority.
    """
    with storage._lock:
        manifest = next(
            (e for e in list_experiments(storage) if e["experiment_id"] == experiment_id), None
        )
        if manifest is None:
            raise ValueError("Unknown experiment")
        constraints = constraints or Constraints(**manifest["constraints"])
        rows = compare_tasks(storage, manifest["task_type"], experiment_id=experiment_id)
        members = []
        for r in storage.query("SELECT metadata FROM runs"):
            meta = json.loads(r["metadata"] or "{}")
            if meta.get("experiment_id") == experiment_id and not meta.get("parent_run_id"):
                members.append(meta)
    by_policy = {r["policy_version"]: r for r in rows}
    expected = Counter(manifest["cases"])
    integrity = []
    if set(by_policy) != set(manifest["candidates"]) or len(rows) != len(by_policy):
        integrity.append("missing_or_mixed_candidates")
    identities = {(m.get("dataset_id"), m.get("mode")) for m in members}
    if len(identities) != 1:
        integrity.append("mismatched_dataset_or_mode")
    for name in manifest["candidates"]:
        group = [m for m in members if m.get("policy_version") == name]
        if Counter(m.get("case_id") for m in group) != expected:
            integrity.append("unpaired_cases:" + name)
        if any(
            m.get("repetition") != 0
            or m.get("split") != manifest["split"]
            or m.get("evaluator_version") != manifest["evaluator_version"]
            for m in group
        ):
            integrity.append("mismatched_evaluation:" + name)
    baseline = by_policy.get(manifest["baseline"])
    if not baseline or baseline["evaluated"] != baseline["runs"]:
        integrity.append("baseline_incomplete")
    elif baseline["pending_requests"] or baseline["cost_per_success"] is None:
        integrity.append("baseline_cost_unavailable")
    baseline_rate = (baseline or {}).get("success_rate")
    quality_floor = max(
        constraints.min_success_rate, (baseline_rate or 0) - constraints.max_quality_drop
    )
    for row in rows:
        reasons = []
        if row["unique_cases"] < constraints.min_cases:
            reasons.append("insufficient_cases")
        if row["evaluated"] != row["runs"]:
            reasons.append("missing_outcomes")
        if row["pending_requests"] or row["cost_per_success"] is None:
            reasons.append("cost_unavailable")
        if row["success_rate"] is None or row["success_rate"] + 1e-12 < quality_floor:
            reasons.append("quality_below_floor")
        if constraints.max_p95_ms is not None and (
            row["latency_p95_ms"] is None or row["latency_p95_ms"] > constraints.max_p95_ms
        ):
            reasons.append("latency_above_limit")
        row.update(eligible=not reasons and not integrity, reasons=reasons)
    eligible = [r for r in rows if r["eligible"]]
    winner = (
        min(
            eligible,
            key=lambda r: (r["cost_per_success"], r["policy_version"] != manifest["baseline"]),
            default=None,
        )
        if manifest["split"] == "validation"
        else None
    )
    baseline_cost = (baseline or {}).get("cost_per_success")
    savings = (
        1 - winner["cost_per_success"] / baseline_cost
        if winner and baseline_cost and baseline_cost > 0
        else None
    )
    return dict(
        schema_version=1,
        experiment_id=experiment_id,
        task_type=manifest["task_type"],
        split=manifest["split"],
        baseline=manifest["baseline"],
        evaluator_version=manifest["evaluator_version"],
        constraints=asdict(constraints),
        quality_floor=quality_floor,
        integrity_errors=integrity,
        candidates=rows,
        recommendation=winner["policy_version"] if winner else None,
        observed_savings_fraction=savings,
        status=(
            "invalid_experiment"
            if integrity
            else "test_report"
            if manifest["split"] == "test"
            else "recommended"
            if winner
            else "no_eligible_policy"
        ),
        interpretation="Empirical selection on this dataset; validate the frozen policy on "
        "separate test cases before deployment. Costs cover recorded charges only.",
    )


class OutcomeRejected(RuntimeError):
    """Neither candidate produced an accepted business result."""


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class ValidatedCascade:
    """Try an inexpensive candidate, validate, then use one explicit fallback.

    validator(input, answer) must return bool and must not receive ground truth.
    Provider/validator exceptions propagate (including budget rejection); only a
    negative business validation triggers fallback. Wrap execution in @trace.
    """

    def __init__(self, primary, fallback, validator, *, name="validated-cascade-v1"):
        self.primary, self.fallback, self.validator, self.name = primary, fallback, validator, name

    async def __call__(self, payload):
        from reins.core.decorators import _get_runtime

        for stage, agent in (("primary", self.primary), ("fallback", self.fallback)):
            answer = await _resolve(agent(deepcopy(payload)))
            accepted = await _resolve(self.validator(deepcopy(payload), deepcopy(answer)))
            if type(accepted) is not bool:
                raise ValueError("The online validator must return a boolean")
            run = get_current_run()
            if run is not None:
                _get_runtime().monitor.event(
                    run.run_id,
                    "policy_validation",
                    payload={"policy": self.name, "stage": stage, "accepted": accepted},
                )
            if accepted:
                return answer
        raise OutcomeRejected("Both primary and fallback failed business validation")
