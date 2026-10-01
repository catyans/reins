import asyncio

import pytest

from reins.checkpoints import CheckpointStore, UnsettledAttempt, fingerprint


@pytest.mark.asyncio
async def test_concurrent_exact_cache_and_reopen(tmp_path):
    path = tmp_path / "state.sqlite"
    store = CheckpointStore(path)
    calls = []

    async def work():
        calls.append(1)
        await asyncio.sleep(0.01)
        return {"x": 1}

    args = dict(isolation="a", step="extract", version="v1", inputs={"source": "abc"}, execute=work)
    a, b = await asyncio.gather(store.run(**args), store.run(**args))
    assert len(calls) == 1 and not a["reused"] and b["reused"]
    store.close()
    store = CheckpointStore(path)
    assert (await store.run(**args))["reused"]
    for override in [
        {"isolation": "b"},
        {"version": "v2"},
        {"inputs": {"source": "new"}},
        {"dependencies": ["changed"]},
    ]:
        assert not (await store.run(**(args | override)))["reused"]
    store.close()


@pytest.mark.asyncio
async def test_uncertain_attempt_requires_explicit_reconciliation(tmp_path):
    store = CheckpointStore(tmp_path / "db")
    calls = []

    def fail():
        calls.append(1)
        raise TimeoutError()

    args = dict(isolation="a", step="paid", version="1", inputs=1, execute=fail)
    with pytest.raises(TimeoutError):
        await store.run(**args)
    with pytest.raises(UnsettledAttempt):
        await store.run(**args)
    assert len(calls) == 1
    key = fingerprint(["a", "paid", "1", 1, []])
    store.authorize_retry(key, reconciliation_reference="billing-confirmation-1")
    assert (await store.run(**(args | {"execute": lambda: 42})))["value"] == 42
    store.close()


@pytest.mark.asyncio
async def test_dependency_revision_invalidates_even_when_output_same(tmp_path):
    s = CheckpointStore(tmp_path / "db")
    a = await s.run(isolation="a", step="source", version="1", inputs="old", execute=lambda: 1)
    b = await s.run(isolation="a", step="source", version="1", inputs="new", execute=lambda: 1)
    x = await s.run(
        isolation="a",
        step="join",
        version="1",
        inputs=1,
        dependencies=[a["revision"]],
        execute=lambda: 2,
    )
    y = await s.run(
        isolation="a",
        step="join",
        version="1",
        inputs=1,
        dependencies=[b["revision"]],
        execute=lambda: 2,
    )
    assert x["revision"] != y["revision"] and not y["reused"]
    s.close()
