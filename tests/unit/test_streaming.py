"""Tests for streaming wrapper."""

import asyncio
from types import SimpleNamespace

from reins.core.instrumentor import _AsyncStreamWrapper, _StreamWrapper
from reins.core.models import SpanData


class FakeInstrumentor:
    """Minimal fake to capture finalize calls."""

    def __init__(self):
        self.finalized = []

    def _finalize_span(self, span, run):
        self.finalized.append((span, run))


def test_sync_stream_wrapper():
    """Stream wrapper should yield all events and capture usage at the end."""
    events = [
        SimpleNamespace(type="content_block_delta"),
        SimpleNamespace(type="content_block_delta"),
        SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        ),
    ]

    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    fake = FakeInstrumentor()
    wrapper = _StreamWrapper(iter(events), span, None, fake)

    collected = list(wrapper)
    assert len(collected) == 3
    assert len(fake.finalized) == 1
    assert fake.finalized[0][0].tokens_in == 100
    assert fake.finalized[0][0].tokens_out == 50


def test_sync_stream_wrapper_no_usage():
    """Should still finalize even without usage info."""
    events = [SimpleNamespace(type="text")]
    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    fake = FakeInstrumentor()

    wrapper = _StreamWrapper(iter(events), span, None, fake)
    list(wrapper)
    assert len(fake.finalized) == 1


def test_sync_stream_context_manager():
    """Should work as a context manager."""
    events = [SimpleNamespace(type="text")]
    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    fake = FakeInstrumentor()

    wrapper = _StreamWrapper(iter(events), span, None, fake)
    with wrapper as w:
        collected = list(w)
    assert len(collected) == 1
    assert len(fake.finalized) == 1


def test_async_stream_wrapper():
    """Async stream wrapper should yield all events and capture usage."""

    async def async_gen():
        yield SimpleNamespace(type="text")
        yield SimpleNamespace(
            type="message_stop",
            usage=SimpleNamespace(input_tokens=200, output_tokens=100),
        )

    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    fake = FakeInstrumentor()
    wrapper = _AsyncStreamWrapper(async_gen(), span, None, fake)

    async def run():
        collected = []
        async for event in wrapper:
            collected.append(event)
        return collected

    collected = asyncio.run(run())
    assert len(collected) == 2
    assert len(fake.finalized) == 1
    assert fake.finalized[0][0].tokens_in == 200


def test_partial_anthropic_usage_does_not_settle():
    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    wrapper = _StreamWrapper(
        iter(
            [
                SimpleNamespace(
                    type="message_start",
                    message=SimpleNamespace(
                        usage=SimpleNamespace(input_tokens=100, output_tokens=0)
                    ),
                )
            ]
        ),
        span,
        None,
        FakeInstrumentor(),
    )
    list(wrapper)
    assert span.metadata.get("cost_status") != "known"


def test_close_early_finalizes_once_without_usage_claim():
    fake = FakeInstrumentor()
    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    wrapper = _StreamWrapper(iter([SimpleNamespace(type="text")]), span, None, fake)
    next(wrapper)
    wrapper.close()
    wrapper.close()
    assert len(fake.finalized) == 1
    assert span.metadata.get("cost_status") != "known"


def test_stream_failure_finalizes_pending():
    def stream():
        yield SimpleNamespace(type="text")
        raise ConnectionError("cut off")

    fake = FakeInstrumentor()
    span = SpanData(provider="anthropic", model="claude-sonnet-4")
    wrapper = _StreamWrapper(stream(), span, None, fake)
    import pytest

    with pytest.raises(ConnectionError):
        list(wrapper)
    assert len(fake.finalized) == 1 and span.status == "error"
    assert span.metadata.get("cost_status") != "known"
