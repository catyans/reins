"""Deadline-aware, isolated microbatches for a single asyncio runtime.

The handler owns provider instrumentation and admission. Submitted jobs are not a
distributed durable queue; use checkpoints for recoverable workflow steps.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import math
import time
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class BatchKey:
    isolation: str
    task_type: str
    schema_version: str
    policy_version: str
    configuration: str

    def __post_init__(self):
        if any(not isinstance(x, str) or not x for x in self.__dict__.values()):
            raise ValueError("All batch isolation fields must be nonempty strings")


@dataclass(frozen=True)
class Delivery:
    value: object
    batch_id: str
    batch_items: int
    queue_ms: float
    service_ms: float
    total_ms: float
    deadline_met: bool
    dispatch_reason: str


@dataclass
class _Item:
    payload: object
    key: BatchKey
    future: asyncio.Future
    submitted: float
    deadline: float


class DeadlineBatcher:
    """Group matching inputs; flush on capacity, wait limit, or deadline.

    ``handler(key, payloads)`` returns one result per input in order. It must
    validate provider output identities itself (e.g. MicroBatchPolicy). A caller
    cancellation removes undispatched work but never cancels a shared paid call.
    A late response remains available with deadline_met=False; queued expired
    items are rejected before dispatch. Handler exceptions are not retried.
    """

    def __init__(
        self,
        handler,
        *,
        batch_size=4,
        max_wait_ms=50,
        concurrency=4,
        service_estimate_ms=0,
        max_pending=4096,
    ):
        if type(batch_size) is not int or not 1 <= batch_size <= 64:
            raise ValueError("batch_size must be in [1, 64]")
        if type(concurrency) is not int or concurrency < 1:
            raise ValueError("concurrency must be positive")
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("max_pending must be positive")
        if any(not math.isfinite(x) or x < 0 for x in (max_wait_ms, service_estimate_ms)):
            raise ValueError("Timing limits must be finite and nonnegative")
        self.handler, self.batch_size = handler, batch_size
        self.wait = max_wait_ms / 1000
        self.estimate = service_estimate_ms / 1000
        self.concurrency, self.max_pending = concurrency, max_pending
        self._pending, self._active = [], set()
        self._event = asyncio.Event()
        self._pump_task = None
        self._closed = False

    async def submit(self, payload, *, key: BatchKey, timeout_seconds=60):
        if self._closed:
            raise RuntimeError("Batcher is closed")
        if not isinstance(key, BatchKey):
            raise TypeError("An explicit BatchKey is required")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if len(self._pending) >= self.max_pending:
            raise RuntimeError("Batch queue capacity exceeded")
        loop = asyncio.get_running_loop()
        if self._pump_task is None:
            # Never inherit the first caller's trace, tenant or budget context.
            self._pump_task = contextvars.Context().run(asyncio.create_task, self._pump())
        future = loop.create_future()
        now = time.monotonic()
        self._pending.append(_Item(payload, key, future, now, now + timeout_seconds))
        self._event.set()
        try:
            return await future
        finally:
            self._event.set()

    async def _execute(self, items, reason):
        started, bid = time.monotonic(), str(uuid4())
        try:
            result = self.handler(items[0].key, [x.payload for x in items])
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, (list, tuple)) or len(result) != len(items):
                raise ValueError("Batch handler must preserve input correspondence and length")
            ended = time.monotonic()
            for item, value in zip(items, result):
                if not item.future.done():
                    item.future.set_result(
                        Delivery(
                            value,
                            bid,
                            len(items),
                            (started - item.submitted) * 1000,
                            (ended - started) * 1000,
                            (ended - item.submitted) * 1000,
                            ended <= item.deadline,
                            reason,
                        )
                    )
        except BaseException as exc:
            for item in items:
                if not item.future.done():
                    if isinstance(exc, asyncio.CancelledError):
                        item.future.cancel()
                    else:
                        item.future.set_exception(exc)
        finally:
            self._event.set()

    async def _pump(self):
        while True:
            self._event.clear()
            self._active = {t for t in self._active if not t.done()}
            now = time.monotonic()
            retained = []
            for item in self._pending:
                if item.future.done():
                    continue
                if item.deadline <= now:
                    item.future.set_exception(TimeoutError("Deadline expired before dispatch"))
                else:
                    retained.append(item)
            self._pending = retained
            if self._closed and not self._pending and not self._active:
                return
            groups = {}
            for item in self._pending:
                groups.setdefault(item.key, []).append(item)
            delay = None
            for items in sorted(groups.values(), key=lambda xs: min(x.deadline for x in xs)):
                items.sort(key=lambda x: (x.deadline, x.submitted))
                chunk = items[: self.batch_size]
                due = min(
                    min(x.submitted + self.wait for x in chunk),
                    min(x.deadline - self.estimate for x in chunk),
                )
                ready = self._closed or len(items) >= self.batch_size or due <= now
                if ready and len(self._active) < self.concurrency:
                    reason = (
                        "drain"
                        if self._closed
                        else "capacity"
                        if len(items) >= self.batch_size
                        else "deadline"
                        if min(x.deadline - self.estimate for x in chunk) <= now
                        else "wait_limit"
                    )
                    ids = {id(x) for x in chunk}
                    self._pending = [x for x in self._pending if id(x) not in ids]
                    self._active.add(asyncio.create_task(self._execute(chunk, reason)))
                    self._event.set()
                elif len(self._active) < self.concurrency:
                    delay = max(0, due - now) if delay is None else min(delay, max(0, due - now))
            if self._pending:
                deadline_delay = max(0.001, min(x.deadline for x in self._pending) - now)
                delay = deadline_delay if delay is None else min(delay, deadline_delay)
            try:
                await asyncio.wait_for(self._event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def aclose(self):
        self._closed = True
        self._event.set()
        if self._pump_task:
            await self._pump_task

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
