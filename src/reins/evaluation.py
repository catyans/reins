"""Repeatable field-extraction evaluation against caller-supplied agent functions."""

from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy

from reins import record_outcome, trace
from reins.core.context import get_current_run


async def evaluate_dataset(
    cases,
    agent,
    *,
    policy_version,
    budget=None,
    task_type="field_extraction",
    repetitions=1,
    mode=None,
    experiment_id=None,
    split="validation",
    evaluator=None,
    evaluator_version="exact-fields-v1",
    on_exceed="degrade",
):
    """agent(input) -> dict (sync or async). Every expected field must match exactly.

    Agents use normal instrumented SDK calls. No model call is made by this harness.
    Callers are responsible for authorised, de-identified fixtures and API spend.
    """
    if not cases or repetitions < 1:
        raise ValueError("Nonempty cases and positive repetitions required")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids) or any(
        not c.get("expected") or not isinstance(c["expected"], dict) for c in cases
    ):
        raise ValueError("Unique case ids and nonempty expected field dictionaries required")
    dataset_id = hashlib.sha256(
        json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    results = []

    @trace(
        agent_name="extraction_pilot",
        task_type=task_type,
        policy_version=policy_version,
        budget=budget,
        on_exceed=on_exceed,
        mode=mode,
    )
    async def evaluate(case, repetition):
        run = get_current_run()
        run.metadata.update(dataset_id=dataset_id, case_id=case["id"], repetition=repetition)
        run.metadata.update(
            experiment_id=experiment_id, split=split, evaluator_version=evaluator_version
        )
        try:
            # Each candidate gets its own input. Expected answers never reach the agent.
            answer = agent(deepcopy(case["input"]))
            if inspect.isawaitable(answer):
                answer = await answer
            if evaluator is None:
                score = sum(
                    isinstance(answer, dict) and k in answer and answer[k] == v
                    for k, v in case["expected"].items()
                ) / len(case["expected"])
            else:
                score = evaluator(answer, deepcopy(case["expected"]))
                if inspect.isawaitable(score):
                    score = await score
            record_outcome(success=score == 1, score=score, reason=evaluator_version)
            return {"run_id": run.run_id, "success": score == 1, "score": score}
        except Exception as exc:
            record_outcome(success=False, score=0, reason=type(exc).__name__)
            raise

    for repetition in range(repetitions):
        for case in cases:
            try:
                results.append(await evaluate(case, repetition))
            except Exception as exc:
                results.append(
                    {"case_id": case["id"], "success": False, "error": type(exc).__name__}
                )
    return {"dataset_id": dataset_id, "results": results}
