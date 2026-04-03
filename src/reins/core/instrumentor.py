"""Auto-instrumentation engine: monkey-patches LLM SDKs."""

from __future__ import annotations

import logging
from typing import Any, Callable

from reins.core.context import get_current_run
from reins.core.events import EventBus
from reins.core.models import SpanData
from reins.core.module import ReinsModule
from reins.core.storage import Storage

logger = logging.getLogger("reins.instrumentor")


class Instrumentor:
    """Monkey-patches LLM SDKs to capture all calls."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        modules: list[ReinsModule],
    ):
        self._event_bus = event_bus
        self._storage = storage
        self._modules = modules
        self._originals: dict[str, Any] = {}
        self._patched = False

    def instrument(self) -> None:
        if self._patched:
            return
        self._instrument_anthropic()
        self._instrument_openai()
        self._patched = True

    def uninstrument(self) -> None:
        if not self._patched:
            return
        self._restore_anthropic()
        self._restore_openai()
        self._originals.clear()
        self._patched = False

    # --- Anthropic ---

    def _instrument_anthropic(self) -> None:
        try:
            import anthropic.resources
        except ImportError:
            return

        orig_create = anthropic.resources.Messages.create
        self._originals["anthropic.Messages.create"] = orig_create

        orig_acreate = getattr(anthropic.resources, "AsyncMessages", None)
        if orig_acreate:
            orig_async = orig_acreate.create
            self._originals["anthropic.AsyncMessages.create"] = orig_async

        inst = self

        def patched_create(self_inner: Any, *args: Any, **kwargs: Any) -> Any:
            return inst._wrap_sync(orig_create, self_inner, "anthropic", args, kwargs)

        anthropic.resources.Messages.create = patched_create  # type: ignore[assignment]

        if orig_acreate:
            original_async_fn = orig_acreate.create

            async def patched_acreate(self_inner: Any, *args: Any, **kwargs: Any) -> Any:
                return await inst._wrap_async(
                    original_async_fn, self_inner, "anthropic", args, kwargs
                )

            orig_acreate.create = patched_acreate  # type: ignore[assignment]

        logger.debug("Instrumented Anthropic SDK")

    def _restore_anthropic(self) -> None:
        try:
            import anthropic.resources
        except ImportError:
            return

        orig = self._originals.get("anthropic.Messages.create")
        if orig:
            anthropic.resources.Messages.create = orig  # type: ignore[assignment]

        orig_async_cls = getattr(anthropic.resources, "AsyncMessages", None)
        orig_async = self._originals.get("anthropic.AsyncMessages.create")
        if orig_async_cls and orig_async:
            orig_async_cls.create = orig_async  # type: ignore[assignment]

    # --- OpenAI ---

    def _instrument_openai(self) -> None:
        try:
            import openai.resources.chat.completions as oai_completions
        except ImportError:
            return

        orig_create = oai_completions.Completions.create
        self._originals["openai.Completions.create"] = orig_create

        inst = self

        def patched_create(self_inner: Any, *args: Any, **kwargs: Any) -> Any:
            return inst._wrap_sync(orig_create, self_inner, "openai", args, kwargs)

        oai_completions.Completions.create = patched_create  # type: ignore[assignment]

        # Async
        orig_async = getattr(oai_completions, "AsyncCompletions", None)
        if orig_async:
            orig_async_fn = orig_async.create
            self._originals["openai.AsyncCompletions.create"] = orig_async_fn

            async def patched_async(self_inner: Any, *args: Any, **kwargs: Any) -> Any:
                return await inst._wrap_async(
                    orig_async_fn, self_inner, "openai", args, kwargs
                )

            orig_async.create = patched_async  # type: ignore[assignment]

        logger.debug("Instrumented OpenAI SDK")

    def _restore_openai(self) -> None:
        try:
            import openai.resources.chat.completions as oai_completions
        except ImportError:
            return

        orig = self._originals.get("openai.Completions.create")
        if orig:
            oai_completions.Completions.create = orig  # type: ignore[assignment]

        orig_async_cls = getattr(oai_completions, "AsyncCompletions", None)
        orig_async = self._originals.get("openai.AsyncCompletions.create")
        if orig_async_cls and orig_async:
            orig_async_cls.create = orig_async  # type: ignore[assignment]

    # --- Wrapping logic ---

    def _wrap_sync(
        self,
        original_fn: Callable,
        client: Any,
        provider: str,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        is_stream = kwargs.get("stream", False)
        span = SpanData.from_llm_call(provider, kwargs)
        run = get_current_run()
        self._enrich_span_from_run(span, run)

        # Pre-hooks (e.g. Budget modifies model)
        for module in self._modules:
            try:
                span = module.on_span_start(span)
            except Exception:
                logger.debug("Module on_span_start error", exc_info=True)
                raise

        # Apply modifications
        if span.degraded:
            kwargs["model"] = span.model

        try:
            response = original_fn(client, *args, **kwargs)

            if is_stream:
                # Wrap the stream to capture usage when it finishes
                return _StreamWrapper(response, span, run, self)

            if provider == "anthropic":
                span.complete_from_anthropic(response)
            else:
                span.complete_from_openai(response)
        except Exception as e:
            span.mark_error(e)
            self._finalize_span(span, run)
            raise

        self._finalize_span(span, run)
        return response

    async def _wrap_async(
        self,
        original_fn: Callable,
        client: Any,
        provider: str,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        is_stream = kwargs.get("stream", False)
        span = SpanData.from_llm_call(provider, kwargs)
        run = get_current_run()
        self._enrich_span_from_run(span, run)

        for module in self._modules:
            try:
                span = module.on_span_start(span)
            except Exception:
                logger.debug("Module on_span_start error", exc_info=True)
                raise

        if span.degraded:
            kwargs["model"] = span.model

        try:
            response = await original_fn(client, *args, **kwargs)

            if is_stream:
                return _AsyncStreamWrapper(response, span, run, self)

            if provider == "anthropic":
                span.complete_from_anthropic(response)
            else:
                span.complete_from_openai(response)
        except Exception as e:
            span.mark_error(e)
            self._finalize_span(span, run)
            raise

        self._finalize_span(span, run)
        return response

    @staticmethod
    def _enrich_span_from_run(span: SpanData, run: Any) -> None:
        """Propagate run-level metadata (agent_name, on_exceed, budget) to span."""
        if run is None:
            return
        span.run_id = run.run_id
        span.metadata["agent_name"] = run.agent_name
        # Propagate on_exceed strategy so Budget engine can read it
        if "on_exceed" in run.metadata:
            span.metadata["on_exceed"] = run.metadata["on_exceed"]
        # Propagate budget limit for run-level budget enforcement
        if run.budget_limit is not None:
            span.metadata["budget_limit"] = str(run.budget_limit)

    def _finalize_span(self, span: SpanData, run: Any) -> None:
        """Post-hooks + persist."""
        for module in self._modules:
            try:
                module.on_span_end(span)
            except Exception:
                logger.debug("Module on_span_end error", exc_info=True)

        self._event_bus.emit("core.span_end", span=span)

        if run:
            run.add_span_cost(span)

        try:
            self._storage.insert_span(span)
        except Exception:
            logger.debug("Failed to persist span", exc_info=True)


class _StreamWrapper:
    """Wraps a sync streaming response to capture usage when the stream ends."""

    def __init__(self, stream: Any, span: SpanData, run: Any, instrumentor: Instrumentor):
        self._stream = stream
        self._span = span
        self._run = run
        self._instrumentor = instrumentor
        self._usage: dict[str, int] = {}

    def __iter__(self):
        return self

    def __next__(self):
        try:
            event = next(self._stream)
            self._extract_usage(event)
            return event
        except StopIteration:
            self._finalize()
            raise

    def __enter__(self):
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        self._finalize()
        if hasattr(self._stream, "__exit__"):
            return self._stream.__exit__(*args)
        return False

    def __getattr__(self, name: str):
        return getattr(self._stream, name)

    def _extract_usage(self, event: Any) -> None:
        # Anthropic: MessageStreamEvent with usage in message_delta or message_stop
        usage = getattr(event, "usage", None)
        if usage:
            if hasattr(usage, "input_tokens"):
                self._usage["input_tokens"] = getattr(usage, "input_tokens", 0)
            if hasattr(usage, "output_tokens"):
                self._usage["output_tokens"] = getattr(usage, "output_tokens", 0)

        # Also check message attribute for final usage
        msg = getattr(event, "message", None)
        if msg:
            msg_usage = getattr(msg, "usage", None)
            if msg_usage:
                self._usage["input_tokens"] = getattr(msg_usage, "input_tokens", 0)
                self._usage["output_tokens"] = getattr(msg_usage, "output_tokens", 0)

    def _finalize(self) -> None:
        if self._usage:
            self._span.complete_from_usage_dict(self._usage)
        else:
            self._span.ended_at = __import__("reins.core.models", fromlist=["_utcnow"])._utcnow()
        self._instrumentor._finalize_span(self._span, self._run)


class _AsyncStreamWrapper:
    """Wraps an async streaming response to capture usage when the stream ends."""

    def __init__(self, stream: Any, span: SpanData, run: Any, instrumentor: Instrumentor):
        self._stream = stream
        self._span = span
        self._run = run
        self._instrumentor = instrumentor
        self._usage: dict[str, int] = {}

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            event = await self._stream.__anext__()
            self._extract_usage(event)
            return event
        except StopAsyncIteration:
            self._finalize()
            raise

    async def __aenter__(self):
        if hasattr(self._stream, "__aenter__"):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        self._finalize()
        if hasattr(self._stream, "__aexit__"):
            return await self._stream.__aexit__(*args)
        return False

    def __getattr__(self, name: str):
        return getattr(self._stream, name)

    def _extract_usage(self, event: Any) -> None:
        usage = getattr(event, "usage", None)
        if usage:
            if hasattr(usage, "input_tokens"):
                self._usage["input_tokens"] = getattr(usage, "input_tokens", 0)
            if hasattr(usage, "output_tokens"):
                self._usage["output_tokens"] = getattr(usage, "output_tokens", 0)

        msg = getattr(event, "message", None)
        if msg:
            msg_usage = getattr(msg, "usage", None)
            if msg_usage:
                self._usage["input_tokens"] = getattr(msg_usage, "input_tokens", 0)
                self._usage["output_tokens"] = getattr(msg_usage, "output_tokens", 0)

    def _finalize(self) -> None:
        if self._usage:
            self._span.complete_from_usage_dict(self._usage)
        else:
            self._span.ended_at = __import__("reins.core.models", fromlist=["_utcnow"])._utcnow()
        self._instrumentor._finalize_span(self._span, self._run)


class _StreamWrapper:
    """Wraps a sync streaming response to capture usage when the stream ends.

    Transparently yields all chunks from the underlying stream.
    When the stream finishes, extracts usage from the final message event
    and finalizes the span.
    """

    def __init__(self, stream: Any, span: SpanData, run: Any, instrumentor: Instrumentor):
        self._stream = stream
        self._span = span
        self._run = run
        self._instrumentor = instrumentor
        self._usage: dict[str, int] = {}
        self._finalized = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            event = next(self._stream)
            self._extract_usage(event)
            return event
        except StopIteration:
            self._finalize()
            raise

    def __enter__(self):
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        self._finalize()
        if hasattr(self._stream, "__exit__"):
            return self._stream.__exit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def _extract_usage(self, event: Any) -> None:
        """Extract usage from streaming events (Anthropic message_delta / OpenAI chunk)."""
        # Anthropic: event.type == "message_delta", event.usage
        if hasattr(event, "usage") and event.usage:
            usage = event.usage
            if hasattr(usage, "input_tokens"):
                self._usage["input_tokens"] = getattr(usage, "input_tokens", 0)
            if hasattr(usage, "output_tokens"):
                self._usage["output_tokens"] = getattr(usage, "output_tokens", 0)

        # Anthropic MessageStream final message
        if hasattr(event, "message") and hasattr(event.message, "usage"):
            u = event.message.usage
            self._usage["input_tokens"] = getattr(u, "input_tokens", 0)
            self._usage["output_tokens"] = getattr(u, "output_tokens", 0)

        # OpenAI: chunk.usage (only in final chunk when stream_options.include_usage=True)
        if hasattr(event, "choices") and hasattr(event, "usage") and event.usage:
            self._usage["prompt_tokens"] = getattr(event.usage, "prompt_tokens", 0)
            self._usage["completion_tokens"] = getattr(event.usage, "completion_tokens", 0)

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self._usage:
            self._span.complete_from_usage_dict(self._usage)
        else:
            self._span.ended_at = __import__("reins.core.models", fromlist=["_utcnow"])._utcnow()
        self._instrumentor._finalize_span(self._span, self._run)


class _AsyncStreamWrapper:
    """Wraps an async streaming response to capture usage when the stream ends."""

    def __init__(self, stream: Any, span: SpanData, run: Any, instrumentor: Instrumentor):
        self._stream = stream
        self._span = span
        self._run = run
        self._instrumentor = instrumentor
        self._usage: dict[str, int] = {}
        self._finalized = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            event = await self._stream.__anext__()
            self._extract_usage(event)
            return event
        except StopAsyncIteration:
            self._finalize()
            raise

    async def __aenter__(self):
        if hasattr(self._stream, "__aenter__"):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        self._finalize()
        if hasattr(self._stream, "__aexit__"):
            return await self._stream.__aexit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def _extract_usage(self, event: Any) -> None:
        """Same logic as sync version."""
        if hasattr(event, "usage") and event.usage:
            usage = event.usage
            if hasattr(usage, "input_tokens"):
                self._usage["input_tokens"] = getattr(usage, "input_tokens", 0)
            if hasattr(usage, "output_tokens"):
                self._usage["output_tokens"] = getattr(usage, "output_tokens", 0)

        if hasattr(event, "message") and hasattr(event.message, "usage"):
            u = event.message.usage
            self._usage["input_tokens"] = getattr(u, "input_tokens", 0)
            self._usage["output_tokens"] = getattr(u, "output_tokens", 0)

        if hasattr(event, "choices") and hasattr(event, "usage") and event.usage:
            self._usage["prompt_tokens"] = getattr(event.usage, "prompt_tokens", 0)
            self._usage["completion_tokens"] = getattr(event.usage, "completion_tokens", 0)

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self._usage:
            self._span.complete_from_usage_dict(self._usage)
        else:
            self._span.ended_at = __import__("reins.core.models", fromlist=["_utcnow"])._utcnow()
        self._instrumentor._finalize_span(self._span, self._run)
