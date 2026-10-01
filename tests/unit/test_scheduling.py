import asyncio
import contextvars

import pytest

from reins.scheduling import BatchKey, DeadlineBatcher


def key(tenant="a"):
    return BatchKey(tenant, "extract", "schema-v1", "policy-v1", "lite")


@pytest.mark.asyncio
async def test_isolation_capacity_tail_and_timing():
    calls = []

    async def handler(k, items):
        calls.append((k, list(items)))
        await asyncio.sleep(0.005)
        return [x * 2 for x in items]

    async with DeadlineBatcher(handler, batch_size=2, max_wait_ms=10) as b:
        results = await asyncio.gather(*(b.submit(i, key=key(str(i % 2))) for i in range(7)))
    assert [r.value for r in results] == list(range(0, 14, 2))
    assert len(calls) == 4
    assert all(len(xs) <= 2 and all(str(x % 2) == k.isolation for x in xs) for k, xs in calls)
    assert all(r.total_ms >= r.service_ms and r.queue_ms >= 0 for r in results)
    assert all(r.deadline_met for r in results)


@pytest.mark.asyncio
async def test_concurrency_and_cancel_shared_request():
    active, peak = 0, 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(k, xs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.set()
        await release.wait()
        active -= 1
        return xs

    async with DeadlineBatcher(handler, batch_size=2, concurrency=2, max_wait_ms=20) as b:
        jobs = [asyncio.create_task(b.submit(i, key=key())) for i in range(8)]
        await started.wait()
        await asyncio.sleep(0.01)
        jobs[0].cancel()
        release.set()
        values = await asyncio.gather(*jobs, return_exceptions=True)
    assert isinstance(values[0], asyncio.CancelledError)
    assert [v.value for v in values[1:]] == list(range(1, 8))
    assert peak == 2


@pytest.mark.asyncio
async def test_expired_queue_no_call_and_late_response_recorded():
    calls = []

    async def handler(k, xs):
        calls.extend(xs)
        await asyncio.sleep(0.03)
        return xs

    async with DeadlineBatcher(handler, batch_size=1, concurrency=1) as b:
        first = asyncio.create_task(b.submit(1, key=key(), timeout_seconds=0.01))
        await asyncio.sleep(0.002)
        with pytest.raises(TimeoutError):
            await b.submit(2, key=key(), timeout_seconds=0.005)
        result = await first
    assert calls == [1] and not result.deadline_met


@pytest.mark.asyncio
async def test_exception_propagates_without_retry_and_context_is_detached():
    caller = contextvars.ContextVar("caller", default=None)
    caller.set("tenant-a")
    observed = []

    async def handler(k, xs):
        observed.append(caller.get())
        raise OSError("transport")

    async with DeadlineBatcher(handler, batch_size=1) as b:
        with pytest.raises(OSError):
            await b.submit(1, key=key())
    assert observed == [None]
    with pytest.raises(RuntimeError):
        await b.submit(1, key=key())


@pytest.mark.asyncio
async def test_drain_and_cancel_before_dispatch():
    calls = []
    b = DeadlineBatcher(lambda k, xs: calls.append(xs) or xs, max_wait_ms=1000)
    cancelled = asyncio.create_task(b.submit(0, key=key()))
    task = asyncio.create_task(b.submit(1, key=key()))
    await asyncio.sleep(0.005)
    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    await b.aclose()
    assert (await task).value == 1 and calls == [[1]]
