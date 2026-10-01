import asyncio

import pytest

from reins.batching import MicroBatchPolicy


@pytest.mark.asyncio
async def test_selective_fallback_and_tail():
    batches, repairs = [], []

    async def batch(items):
        batches.append(list(items))
        return {str(i): n * 2 for i, n in enumerate(items) if n != 2}

    def fallback(n):
        repairs.append(n)
        return n * 2

    result = await MicroBatchPolicy(batch, fallback, lambda n, a: a == n * 2, batch_size=3)(
        [0, 1, 2, 3, 4]
    )
    assert result.outputs == [0, 2, 4, 6, 8]
    assert batches == [[0, 1, 2], [3, 4]]
    assert repairs == [2] and result.fallback_indices == [2]


@pytest.mark.asyncio
async def test_corrupted_ids_and_invalid_fallback():
    policy = MicroBatchPolicy(lambda x: {"99": 4}, lambda x: -1, lambda x, a: a == x)
    result = await policy([1, 2])
    assert result.outputs == [None, None]
    assert len(result.batch_errors) == 3


@pytest.mark.asyncio
async def test_provider_errors_and_cancellation_do_not_start_more_calls():
    called = []

    async def broken(items):
        raise asyncio.CancelledError()

    policy = MicroBatchPolicy(broken, lambda x: called.append(x), lambda x, a: True)
    with pytest.raises(asyncio.CancelledError):
        await policy([1])
    assert called == []


@pytest.mark.asyncio
async def test_empty_and_boolean_validation():
    assert (await MicroBatchPolicy(lambda x: {}, lambda x: x, lambda x, a: True)([])).outputs == []
    with pytest.raises(ValueError, match="boolean"):
        await MicroBatchPolicy(lambda x: {}, lambda x: x, lambda x, a: 1)([1])


def test_identity_alignment_recovers_order_without_guessing():
    from reins.batching import align_by_identity

    rows = [{"id": "b", "value": 2}, {"id": "a", "value": 1}, {"id": "unknown", "value": 3}]
    assert align_by_identity(rows, ["a", "b"], lambda a: a["id"]) == {"1": rows[0], "0": rows[1]}
    rows.append({"id": "a", "value": 99})
    assert align_by_identity(rows, ["a", "b"], lambda a: a["id"]) == {"1": rows[0]}
    with pytest.raises(ValueError):
        align_by_identity(rows, ["a", "a"], lambda a: a["id"])


@pytest.mark.asyncio
async def test_transform_and_input_isolation():
    original = [{"id": "a", "amount": 12}]

    async def batch(items):
        items[0]["amount"] = 999
        return {"0": {"id": "a", "amount": 0}}

    def transform(payload, answer):
        return {**answer, "amount": payload["amount"]}

    policy = MicroBatchPolicy(
        batch, lambda x: None, lambda x, a: a["amount"] == x["amount"], transform=transform
    )
    result = await policy(original)
    assert result.outputs == original
    assert not result.fallback_indices
    assert original[0]["amount"] == 12


def test_batch_selection_quality_cost_and_pairing():
    from reins.batching import select_configuration

    records = [{"id": str(i), "success": True} for i in range(10)]
    base = {
        "policy": "base",
        "records": records,
        "total_cost": 1.0,
        "cost_complete": True,
        "p95_wait_seconds": 1.0,
    }
    cheap = {
        **base,
        "policy": "cheap",
        "total_cost": 0.1,
        "records": [{"id": str(i), "success": i != 0} for i in range(10)],
    }
    batch = {**base, "policy": "batch", "total_cost": 0.4, "p95_wait_seconds": 4.0}
    assert (
        select_configuration([base, cheap, batch], baseline="base", min_cases=10)["recommendation"]
        == "batch"
    )
    assert (
        select_configuration(
            [base, cheap, batch], baseline="base", min_cases=10, max_p95_seconds=2
        )["recommendation"]
        == "base"
    )
    pending = {**batch, "cost_complete": False}
    assert (
        select_configuration([base, pending], baseline="base", min_cases=10)["recommendation"]
        == "base"
    )
    with pytest.raises(ValueError, match="same unique cases"):
        select_configuration(
            [base, {**batch, "records": records[:-1]}], baseline="base", min_cases=10
        )


def test_malformed_identity_is_skipped():
    from reins.batching import align_by_identity

    rows = [{"id": ["bad"]}, {"id": "a"}]
    assert align_by_identity(rows, ["a"], lambda a: a["id"]) == {"0": rows[1]}
