import json

import pytest
from click.testing import CliRunner

from reins import configure, record_external_cost
from reins.cli.main import cli
from reins.core.decorators import _get_runtime, shutdown
from reins.optimization import (
    Constraints,
    OutcomeRejected,
    ValidatedCascade,
    evaluate_experiment,
    recommend,
)


@pytest.fixture
def runtime(tmp_path):
    configure(storage_path=tmp_path / "optimizer.duckdb", mode="observe")
    yield _get_runtime()
    shutdown()


CASES = [{"id": str(i), "input": {"x": i}, "expected": {"x": i}} for i in range(4)]


async def strong(payload):
    record_external_cost("0.10", label="model")
    return payload


async def cheap(payload):
    record_external_cost("0.01", label="model")
    return {"x": 0}


def validator(payload, answer):
    record_external_cost("0.005", label="validation")
    return payload == answer


async def experiment(**kwargs):
    return await evaluate_experiment(
        CASES,
        {"strong": strong, "cheap": cheap, "cascade": ValidatedCascade(cheap, strong, validator)},
        baseline="strong",
        constraints=Constraints(min_cases=4),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_cost_quality_and_fallback_accounting(runtime):
    report = await experiment()
    rows = {r["policy_version"]: r for r in report["candidates"]}
    assert report["recommendation"] == "cascade"
    assert rows["cheap"]["reasons"] == ["quality_below_floor"]
    assert rows["cascade"]["known_total_cost"] == pytest.approx(0.375)
    assert rows["cascade"]["success_rate"] == 1
    assert report["observed_savings_fraction"] == pytest.approx(0.0625)
    events = runtime.storage.query("SELECT * FROM run_events WHERE event_type='policy_validation'")
    assert len(events) == 7


@pytest.mark.asyncio
async def test_test_split_never_selects_and_experiments_do_not_mix(runtime):
    validation = await experiment()
    test = await experiment(split="test")
    assert test["recommendation"] is None and test["status"] == "test_report"
    assert test["observed_savings_fraction"] is None
    reloaded = recommend(runtime.storage, validation["experiment_id"])
    assert all(r["runs"] == 4 for r in reloaded["candidates"])
    assert reloaded["recommendation"] == "cascade"


@pytest.mark.asyncio
async def test_minimum_sample_and_latency_gates(runtime):
    report = await experiment()
    blocked = recommend(runtime.storage, report["experiment_id"], constraints=Constraints())
    assert blocked["recommendation"] is None
    assert all("insufficient_cases" in r["reasons"] for r in blocked["candidates"])
    slow = recommend(
        runtime.storage,
        report["experiment_id"],
        constraints=Constraints(min_cases=4, max_p95_ms=1e-12),
    )
    assert slow["recommendation"] is None
    assert all("latency_above_limit" in r["reasons"] for r in slow["candidates"])


@pytest.mark.asyncio
async def test_duplicate_and_missing_cases_block_selection(runtime):
    report = await experiment()
    row = runtime.storage.query("SELECT run_id, metadata FROM runs LIMIT 1")[0]
    meta = json.loads(row["metadata"])
    meta["case_id"] = "wrong-case"
    runtime.storage.query(
        "UPDATE runs SET metadata=? WHERE run_id=?", [json.dumps(meta), row["run_id"]]
    )
    blocked = recommend(runtime.storage, report["experiment_id"])
    assert blocked["status"] == "invalid_experiment" and blocked["recommendation"] is None
    assert any(e.startswith("unpaired_cases") for e in blocked["integrity_errors"])


@pytest.mark.asyncio
async def test_pending_baseline_blocks_savings(runtime):
    from types import SimpleNamespace

    async def unpriced(payload):
        async def provider(*args, **kwargs):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10))

        await runtime.instrumentor._wrap_async(provider, None, "openai", (), {"model": "unknown"})
        return payload

    report = await evaluate_experiment(
        CASES,
        {"unpriced": unpriced, "cheap": strong},
        baseline="unpriced",
        constraints=Constraints(min_cases=4),
    )
    assert report["recommendation"] is None
    assert "baseline_cost_unavailable" in report["integrity_errors"]


@pytest.mark.asyncio
async def test_input_isolation_and_no_expected_leak(runtime):
    seen = []

    def mutate(payload):
        assert set(payload) == {"x"}
        payload["x"] = 900
        return payload

    def clean(payload):
        seen.append(payload["x"])
        return payload

    await evaluate_experiment(
        CASES,
        {"mutator": mutate, "clean": clean},
        baseline="clean",
        constraints=Constraints(min_cases=4),
    )
    assert seen == [0, 1, 2, 3] and CASES[0]["input"] == {"x": 0}


@pytest.mark.asyncio
async def test_cascade_rejects_invalid_fallback_and_propagates_errors():
    calls = []

    def primary(payload):
        calls.append("primary")
        return {}

    def fallback(payload):
        calls.append("fallback")
        return {}

    with pytest.raises(OutcomeRejected):
        await ValidatedCascade(primary, fallback, lambda p, a: False)({})
    assert calls == ["primary", "fallback"]
    calls.clear()

    def failure(payload):
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await ValidatedCascade(failure, fallback, lambda p, a: True)({})
    assert not calls
    with pytest.raises(ValueError):
        await ValidatedCascade(primary, fallback, lambda p, a: "yes")({})


@pytest.mark.asyncio
async def test_cli_export_and_missing_outcome(runtime, tmp_path):
    report = await experiment()
    database = str(runtime.storage._path)
    runtime.storage.query("DELETE FROM outcomes")
    blocked = recommend(runtime.storage, report["experiment_id"])
    assert blocked["recommendation"] is None
    # A separate experiment remains exportable after the sole writer closes.
    report = await experiment()
    shutdown()
    path = tmp_path / "policy.json"
    result = CliRunner().invoke(
        cli,
        [
            "optimize",
            "--database",
            database,
            "--experiment",
            report["experiment_id"],
            "--output",
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text())["recommendation"] == "cascade"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_success_rate": float("nan")},
        {"max_quality_drop": -1},
        {"min_cases": 0},
        {"min_cases": 1.2},
        {"max_p95_ms": float("inf")},
    ],
)
def test_invalid_constraints(kwargs):
    with pytest.raises(ValueError):
        Constraints(**kwargs)


@pytest.mark.asyncio
async def test_dashboard_experiment_endpoints(runtime):
    from urllib.error import HTTPError
    from urllib.request import urlopen

    from reins.dashboard.server import DashboardServer

    report = await experiment()
    runtime.config.dashboard_port = 0
    server = DashboardServer(runtime)
    try:
        with urlopen(server.url + "/api/experiments") as response:
            entries = json.load(response)
        assert entries[0]["experiment_id"] == report["experiment_id"]
        with urlopen(
            server.url + "/api/optimization?experiment=" + report["experiment_id"]
        ) as response:
            served = json.load(response)
        assert served == report
        with pytest.raises(HTTPError) as error:
            urlopen(server.url + "/api/optimization?experiment=missing")
        assert error.value.code == 400
    finally:
        server.close()


@pytest.mark.asyncio
async def test_explicit_custom_evaluator_and_null_field(runtime):
    from reins.evaluation import evaluate_dataset

    cases = [{"id": "nullable", "input": {}, "expected": {"optional": None}}]
    result = await evaluate_dataset(cases, lambda p: {}, policy_version="missing-field")
    assert result["results"][0]["success"] is False

    with pytest.raises(ValueError, match="explicit version"):
        await evaluate_experiment(
            CASES, {"a": strong, "b": cheap}, baseline="a", evaluator=lambda a, e: 1
        )

    async def evaluate(answer, expected):
        record_external_cost("0.002", label="business evaluator")
        return float(answer == expected)

    report = await evaluate_experiment(
        CASES,
        {"a": strong, "b": cheap},
        baseline="a",
        evaluator=evaluate,
        evaluator_version="business-v1",
        constraints=Constraints(min_cases=4),
    )
    assert report["recommendation"] == "a"
    assert next(r for r in report["candidates"] if r["policy_version"] == "a")[
        "known_total_cost"
    ] == pytest.approx(0.408)
