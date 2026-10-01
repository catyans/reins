"""Bounded microbatches with explicit per-item validation and selective fallback."""

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchResult:
    outputs: list
    fallback_indices: list[int]
    batch_errors: list[str]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class MicroBatchPolicy:
    """Batch independent inputs, then repair missing/invalid items individually.

    batch_agent(items) returns a mapping of local string index to answer. The
    validator sees only input and answer. No reference labels are passed to either
    callable. An optional transform can apply deterministic business calculations
    before validation to primary and fallback outputs. Transport exceptions
    propagate; shape errors trigger selective
    fallback. Caller owns tracing/accounting and any output-token limit.
    """

    def __init__(self, batch_agent, fallback, validator, *, batch_size=8, transform=None):
        if type(batch_size) is not int or not 1 <= batch_size <= 64:
            raise ValueError("batch_size must be an integer in [1, 64]")
        self.batch_agent = batch_agent
        self.fallback = fallback
        self.validator = validator
        self.batch_size = batch_size
        self.transform = transform

    async def __call__(self, inputs):
        outputs, fallback_indices, errors = [], [], []
        for start in range(0, len(inputs), self.batch_size):
            chunk = deepcopy(inputs[start : start + self.batch_size])
            raw = await _resolve(self.batch_agent(deepcopy(chunk)))
            if not isinstance(raw, dict):
                raw = {}
                errors.append(f"batch_{start}:invalid_mapping")
            if set(raw) - {str(i) for i in range(len(chunk))}:
                errors.append(f"batch_{start}:unexpected_ids")
                raw = {}  # Never guess correspondence when identifiers are corrupted.
            for i, payload in enumerate(chunk):
                answer = raw.get(str(i))
                if self.transform is not None:
                    answer = await _resolve(self.transform(deepcopy(payload), answer))
                valid = await _resolve(self.validator(deepcopy(payload), deepcopy(answer)))
                if type(valid) is not bool:
                    raise ValueError("validator must return a boolean")
                if not valid:
                    fallback_indices.append(start + i)
                    answer = await _resolve(self.fallback(deepcopy(payload)))
                    if self.transform is not None:
                        answer = await _resolve(self.transform(deepcopy(payload), answer))
                    valid = await _resolve(self.validator(deepcopy(payload), deepcopy(answer)))
                    if type(valid) is not bool:
                        raise ValueError("validator must return a boolean")
                    if not valid:
                        errors.append(f"item_{start + i}:fallback_invalid")
                        answer = None
                outputs.append(answer)
        return BatchResult(outputs, fallback_indices, errors)


def align_by_identity(raw, identities, identity):
    """Recover JSON list/map shapes by a caller-defined business identifier.

    Never use list order as an implicit association. Unexpected identities are
    ignored; duplicates are removed so the caller can retry them. Input identities
    must be unique. No expected outputs are used.
    """
    if len(set(identities)) != len(identities):
        raise ValueError("Input identities must be unique")
    positions = {value: str(i) for i, value in enumerate(identities)}
    values = list(raw.values()) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    aligned, seen, duplicate = {}, set(), set()
    for value in values:
        key = identity(value)
        try:
            if key not in positions:
                continue
        except TypeError:
            continue
        index = positions[key]
        if key in seen:
            duplicate.add(index)
        else:
            seen.add(key)
            aligned[index] = value
    for index in duplicate:
        aligned.pop(index, None)
    return aligned


def select_configuration(
    reports, *, baseline, min_acceptance=0.95, min_cases=30, max_p95_seconds=None
):
    """Select on paired validation records, never by cost alone.

    Records must contain unique case IDs and Boolean success values. The caller
    provides total settled cost and batch-service p95. This helper does not
    collect billing or certify statistical non-inferiority. Keep a separate audit.
    """
    import math

    if type(min_cases) is not int or min_cases < 1:
        raise ValueError("min_cases must be a positive integer")
    if not math.isfinite(min_acceptance) or not 0 <= min_acceptance <= 1:
        raise ValueError("min_acceptance must be in [0, 1]")
    if max_p95_seconds is not None and (not math.isfinite(max_p95_seconds) or max_p95_seconds <= 0):
        raise ValueError("max_p95_seconds must be positive")
    by_name = {r["policy"]: r for r in reports}
    if len(by_name) != len(reports) or baseline not in by_name:
        raise ValueError("Unique policies including the baseline are required")
    reference = by_name[baseline]["records"]
    ids = {r["id"] for r in reference}
    if not ids or len(ids) != len(reference):
        raise ValueError("Unique nonempty reference cases required")
    baseline_rate = sum(r["success"] is True for r in reference) / len(reference)
    floor = max(min_acceptance, baseline_rate)
    decisions = []
    for name, report in by_name.items():
        records = report["records"]
        if len(records) != len(ids) or {r["id"] for r in records} != ids:
            raise ValueError("All candidates must contain the same unique cases")
        if any(type(r["success"]) is not bool for r in records):
            raise ValueError("Business outcomes must be Boolean")
        success = sum(r["success"] for r in records)
        reasons = []
        if len(records) < min_cases:
            reasons.append("insufficient_cases")
        if by_name[baseline].get("cost_complete") is not True:
            reasons.append("baseline_cost_unavailable")
        cost = report.get("total_cost")
        if (
            report.get("cost_complete") is not True
            or cost is None
            or not math.isfinite(cost)
            or cost < 0
        ):
            reasons.append("unsettled_cost")
        if success / len(records) < floor:
            reasons.append("quality_below_floor")
        latency = report.get("p95_wait_seconds")
        if max_p95_seconds is not None and (
            latency is None or not math.isfinite(latency) or latency > max_p95_seconds
        ):
            reasons.append("latency_above_limit")
        decisions.append(
            {
                "policy": name,
                "eligible": not reasons and success > 0,
                "reasons": reasons,
                "cost_per_accepted": cost / success if not reasons and success else None,
            }
        )
    eligible = [d for d in decisions if d["eligible"]]
    winner = min(
        eligible, key=lambda d: (d["cost_per_accepted"], d["policy"] != baseline), default=None
    )
    return {
        "recommendation": winner["policy"] if winner else None,
        "quality_floor": floor,
        "decisions": decisions,
        "max_p95_seconds": max_p95_seconds,
    }
