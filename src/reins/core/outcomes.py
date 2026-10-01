"""Task-level quality and cost comparison, including failed attempts."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean


def record_outcome(
    *, success: bool, score: float | None = None, reason: str = "", run_id: str | None = None
):
    """Record an application-verified outcome; never infer quality from HTTP success."""
    from reins.core.context import get_current_run
    from reins.core.decorators import _get_runtime

    if type(success) is not bool:
        raise ValueError("success must be a boolean")
    if score is not None and (not math.isfinite(score) or not 0 <= score <= 1):
        raise ValueError("score must be between zero and one")
    current = get_current_run()
    run_id = run_id or (current.run_id if current else None)
    storage = _get_runtime().storage
    if not run_id or not storage.query("SELECT run_id FROM runs WHERE run_id=?", [run_id]):
        raise ValueError("record_outcome requires an existing run")
    storage.query(
        """INSERT INTO outcomes VALUES (?,?,?,?,?) ON CONFLICT(run_id)
        DO UPDATE SET success=excluded.success, score=excluded.score,
        reason=excluded.reason, recorded_at=excluded.recorded_at""",
        [run_id, success, score, reason, datetime.now(timezone.utc).isoformat()],
    )
    return run_id


def record_external_cost(amount, *, label: str, run_id: str | None = None):
    """Report an already incurred tool/evaluation cost; not a spending authorization."""
    from uuid import uuid4

    from reins.core.context import get_current_run
    from reins.core.decorators import _get_runtime

    value = Decimal(str(amount))
    if not value.is_finite() or value < 0:
        raise ValueError("External cost must be finite and nonnegative")
    current = get_current_run()
    run_id = run_id or (current.run_id if current else None)
    storage = _get_runtime().storage
    if not run_id or not storage.query("SELECT run_id FROM runs WHERE run_id=?", [run_id]):
        raise ValueError("An existing run is required")
    storage.query(
        "INSERT INTO external_costs VALUES (?,?,?,?)", [str(uuid4()), run_id, label, value]
    )


def reconcile_cost(span_id: str, actual_cost):
    """Settle a pending provider request after checking provider billing/usage."""
    from reins.core.decorators import _get_runtime

    for module in _get_runtime().modules:
        if module.name == "budget":
            module._engine.reconcile(span_id, actual_cost)
            return
    raise RuntimeError("Budget module is not loaded")


def compare_tasks(storage, task_type=None, *, experiment_id=None):
    """JSON-serializable cohorts. Pending charges suppress cost-per-success claims."""
    runs = storage.query("SELECT * FROM runs ORDER BY started_at")
    metadata = {r["run_id"]: json.loads(r["metadata"] or "{}") for r in runs}
    costs = defaultdict(lambda: Decimal("0"))
    pending = defaultdict(int)
    calls = defaultdict(int)
    degraded = defaultdict(int)
    decisions = defaultdict(lambda: defaultdict(int))
    for request in storage.query("SELECT * FROM budget_requests"):
        run = request["run_id"]
        root = metadata.get(run, {}).get("root_run_id", run)
        calls[root] += 1
        if request["actual"] is None:
            pending[root] += 1
        else:
            costs[root] += request["actual"]
    for item in storage.query("SELECT * FROM external_costs"):
        root = metadata.get(item["run_id"], {}).get("root_run_id", item["run_id"])
        costs[root] += item["amount"]
    for span in storage.query("SELECT run_id, degraded, metadata FROM spans WHERE span_type='llm'"):
        root = metadata.get(span["run_id"], {}).get("root_run_id", span["run_id"])
        degraded[root] += int(bool(span["degraded"]))
        decision = json.loads(span["metadata"] or "{}").get("budget_decision", "unclassified")
        decisions[root][decision] += 1
    outcomes = {r["run_id"]: r for r in storage.query("SELECT * FROM outcomes")}
    cohorts = defaultdict(list)
    for run in runs:
        meta = metadata[run["run_id"]]
        if experiment_id is not None and meta.get("experiment_id") != experiment_id:
            continue
        if meta.get("parent_run_id"):
            continue
        task = meta.get("task_type", "default")
        if task_type is not None and task != task_type:
            continue
        cohorts[
            (
                task,
                meta.get("policy_version", "baseline"),
                meta.get("mode", "observe"),
                meta.get("dataset_id", "unlabelled"),
                meta.get("experiment_id"),
                meta.get("split", "unspecified"),
            )
        ].append(run)
    result = []
    for (task, policy, mode, dataset, experiment, split), members in cohorts.items():
        ids = [r["run_id"] for r in members]
        finished = [r for r in members if r["ended_at"]]
        assessed = [r for r in finished if r["run_id"] in outcomes or r["status"] == "failed"]
        successes = sum(
            r["status"] == "completed" and outcomes.get(r["run_id"], {}).get("success", False)
            for r in assessed
        )
        unknown = sum(pending[i] for i in ids)
        total = sum((costs[i] for i in ids), Decimal("0"))
        n = len(assessed)
        rate = successes / n if n else None
        interval = None
        if n:
            z = 1.96
            center = (rate + z * z / (2 * n)) / (1 + z * z / n)
            spread = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / (1 + z * z / n)
            interval = [max(0, center - spread), min(1, center + spread)]
        scores = [
            outcomes[i]["score"] for i in ids if i in outcomes and outcomes[i]["score"] is not None
        ]
        durations = sorted(
            (
                datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])
            ).total_seconds()
            * 1000
            for r in finished
        )
        failure_reasons = defaultdict(int)
        for i in ids:
            reason = metadata[i].get("failure_reason")
            if reason:
                failure_reasons[reason] += 1
        event_counts = defaultdict(int)
        for i in ids:
            for event, count in decisions[i].items():
                event_counts[event] += count
        complete = len(assessed) == len(members) and not unknown
        result.append(
            dict(
                task_type=task,
                policy_version=policy,
                mode=mode,
                dataset_id=dataset,
                experiment_id=experiment,
                split=split,
                unique_cases=len({metadata[i].get("case_id", i) for i in ids}),
                reported_retries=sum(metadata[i].get("retries", 0) for i in ids),
                runs=len(ids),
                evaluated=n,
                success_count=successes,
                success_rate=rate,
                success_rate_95ci=interval,
                average_score=mean(scores) if scores else None,
                known_total_cost=float(total),
                pending_requests=unknown,
                cost_per_success=float(total / successes) if complete and successes else None,
                llm_calls=sum(calls[i] for i in ids),
                degraded_calls=sum(degraded[i] for i in ids),
                decisions=dict(event_counts),
                failure_reasons=dict(failure_reasons),
                latency_p95_ms=durations[math.ceil(0.95 * len(durations)) - 1]
                if durations
                else None,
            )
        )
    return sorted(
        result, key=lambda r: (r["task_type"], r["dataset_id"], r["policy_version"], r["mode"])
    )


def record_retry():
    """Count application retries explicitly; repeated LLM calls are not necessarily retries."""
    from reins.core.context import get_current_run

    run = get_current_run()
    if run is None:
        raise ValueError("record_retry requires a traced task")
    run.metadata["retries"] = run.metadata.get("retries", 0) + 1
    from reins.core.decorators import _get_runtime

    runtime = _get_runtime()
    runtime.storage.update_run(run)
    runtime.monitor.event(run.run_id, "retry", payload={"count": run.metadata["retries"]})
