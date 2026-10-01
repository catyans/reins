import asyncio
from types import SimpleNamespace

import pytest

from reins import configure, record_external_cost, record_outcome, record_retry, trace
from reins.core.context import get_current_run
from reins.core.decorators import _get_runtime, shutdown
from reins.core.outcomes import compare_tasks
from reins.evaluation import evaluate_dataset


@pytest.fixture
def runtime(tmp_path):
    configure(
        storage_path=tmp_path / "pilot.db",
        mode="enforce",
        prices={"openai": {"fake": ["10", "10"]}},
        token_counter=lambda *args: 100,
    )
    yield _get_runtime()
    shutdown()


async def call(runtime, fail=False):
    async def provider(client, **kwargs):
        if fail:
            raise TimeoutError("usage unavailable")
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100))

    return await runtime.instrumentor._wrap_async(
        provider,
        None,
        "openai",
        (),
        {"model": "fake", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 100},
    )


@pytest.mark.asyncio
async def test_nested_context_and_report_no_double_charge(runtime):
    @trace(budget=".01")
    async def inner():
        await call(runtime)

    @trace(budget=".02", task_type="extract", policy_version="p1")
    async def outer():
        root = get_current_run()
        await inner()
        assert get_current_run() is root
        await call(runtime)
        record_external_cost(".001", label="validator")
        record_retry()
        record_outcome(success=True, score=1)

    await outer()
    assert get_current_run() is None
    report = compare_tasks(runtime.storage)
    assert len(report) == 1 and report[0]["runs"] == 1
    assert report[0]["llm_calls"] == 2 and report[0]["cost_per_success"] == 0.005
    assert report[0]["reported_retries"] == 1


@pytest.mark.asyncio
async def test_pending_suppresses_cost_per_success(runtime):
    @trace(budget=".01")
    async def task():
        try:
            await call(runtime, fail=True)
        except TimeoutError:
            pass
        record_outcome(success=True)

    await task()
    row = compare_tasks(runtime.storage)[0]
    assert row["pending_requests"] == 1 and row["cost_per_success"] is None


@pytest.mark.asyncio
async def test_failures_are_in_cost_numerator(runtime):
    @trace(budget=".01", policy_version="p")
    async def task(success):
        await call(runtime)
        record_outcome(success=success)

    await task(True)
    await task(False)
    row = compare_tasks(runtime.storage)[0]
    assert row["success_rate"] == 0.5 and row["cost_per_success"] == 0.004


@pytest.mark.asyncio
async def test_cancellation_restores_context_retains_reservation(runtime):
    async def provider(*args, **kwargs):
        raise asyncio.CancelledError()

    @trace(budget=".01")
    async def task():
        await runtime.instrumentor._wrap_async(
            provider, None, "openai", (), {"model": "fake", "max_tokens": 100}
        )

    with pytest.raises(asyncio.CancelledError):
        await task()
    assert get_current_run() is None
    assert runtime.storage.query("SELECT status FROM runs")[0]["status"] == "failed"
    assert runtime.storage.query("SELECT actual FROM budget_requests")[0]["actual"] is None


@pytest.mark.asyncio
async def test_harness_versions_share_dataset(runtime):
    async def agent(payload):
        await call(runtime)
        return {"country": payload}

    cases = [{"id": "1", "input": "US", "expected": {"country": "US"}}]
    for version in ["a", "b"]:
        await evaluate_dataset(cases, agent, policy_version=version, budget=".01", repetitions=2)
    rows = compare_tasks(runtime.storage)
    assert len(rows) == 2 and rows[0]["dataset_id"] == rows[1]["dataset_id"]
    assert all(r["runs"] == 2 and r["unique_cases"] == 1 and r["success_rate"] == 1 for r in rows)


@pytest.mark.asyncio
async def test_child_cannot_disable_enforcement(runtime):
    @trace(mode="observe")
    async def child():
        pass

    @trace(mode="enforce")
    async def parent():
        await child()

    with pytest.raises(ValueError):
        await parent()


def test_bad_configuration_is_not_ignored(runtime):
    with pytest.raises(TypeError):
        configure(budget="$5/day")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 2])
def test_invalid_score(runtime, value):
    with pytest.raises(ValueError):
        record_outcome(success=True, score=value)


@pytest.mark.asyncio
async def test_multimodal_rejected_before_provider(runtime):
    from reins.budget.engine import CostBoundError

    called = False

    async def provider(*args, **kwargs):
        nonlocal called
        called = True

    @trace(mode="enforce")
    async def task():
        await runtime.instrumentor._wrap_async(
            provider,
            None,
            "openai",
            (),
            {
                "model": "fake",
                "max_tokens": 100,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://example.invalid/a.png"},
                            }
                        ],
                    }
                ],
            },
        )

    with pytest.raises(CostBoundError):
        await task()
    assert not called


@pytest.mark.asyncio
async def test_observe_unknown_model_no_false_savings(runtime):
    async def provider(*args, **kwargs):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100))

    @trace(mode="observe")
    async def task():
        await runtime.instrumentor._wrap_async(provider, None, "openai", (), {"model": "unpriced"})
        record_outcome(success=True)

    await task()
    row = compare_tasks(runtime.storage)[0]
    assert row["pending_requests"] == 1 and row["cost_per_success"] is None
