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
        config=None,
    ):
        self._event_bus = event_bus
        self._storage = storage
        self._modules = modules
        self._config = config
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
            return inst._wrap_sync(
                orig_create, self_inner, inst._openai_provider(self_inner), args, kwargs
            )

        oai_completions.Completions.create = patched_create  # type: ignore[assignment]

        # Async
        orig_async = getattr(oai_completions, "AsyncCompletions", None)
        if orig_async:
            orig_async_fn = orig_async.create
            self._originals["openai.AsyncCompletions.create"] = orig_async_fn

            async def patched_async(self_inner: Any, *args: Any, **kwargs: Any) -> Any:
                return await inst._wrap_async(
                    orig_async_fn, self_inner, inst._openai_provider(self_inner), args, kwargs
                )

            orig_async.create = patched_async  # type: ignore[assignment]

        logger.debug("Instrumented OpenAI SDK")

    @staticmethod
    def _openai_provider(resource):
        from urllib.parse import urlparse

        client = getattr(resource, "_client", None)
        host = urlparse(str(getattr(client, "base_url", ""))).hostname
        return "google" if host == "generativelanguage.googleapis.com" else "openai"

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
        from reins.control.instrumentation import enrich

        enrich(span)
        span._prices = self._config.prices if self._config else {}
        counter = self._config.token_counter if self._config else None
        if counter:

            def bound_for(model):
                value = counter(provider, model, {**kwargs, "model": model})
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("token_counter must return a nonnegative integer")
                return value

            span.estimated_input_tokens = bound_for(span.model)
            span._bound_counter = bound_for
            span.metadata["input_bound_verified"] = True
        enforced = (
            span.metadata.get("mode", self._config.mode if self._config else "observe") == "enforce"
        )
        if enforced:
            from reins.budget.engine import CostBoundError

            sdk_client = getattr(client, "_client", None)
            if sdk_client is not None and getattr(sdk_client, "max_retries", 0) != 0:
                raise CostBoundError(
                    "Disable hidden SDK retries (max_retries=0); trace retries explicitly"
                )

            def unsupported(value):
                if isinstance(value, dict):
                    if "cache_control" in value or value.get("type") in {
                        "image",
                        "image_url",
                        "input_audio",
                        "audio",
                        "web_search",
                        "web_search_preview",
                    }:
                        return True
                    return any(unsupported(v) for v in value.values())
                return isinstance(value, list) and any(unsupported(v) for v in value)

            if unsupported(kwargs) or kwargs.get("n", 1) != 1:
                raise CostBoundError(
                    "Enforce v1 supports one text completion "
                    "without explicit cache writes/hosted tools"
                )
        if provider in {"openai", "google"} and is_stream:
            kwargs["stream_options"] = {**kwargs.get("stream_options", {}), "include_usage": True}

        self._event_bus.emit("core.span_start", span=span)
        for module in self._modules:
            try:
                span = module.on_span_start(span)
            except Exception as exc:
                span.mark_error(exc)
                span.metadata["budget_decision"] = "rejected"
                self._finalize_span(span, run)
                raise

        # Apply modifications
        if span.degraded:
            kwargs["model"] = span.model
        from reins.control.instrumentation import admit

        try:
            admit(span, self._config)
        except Exception as exc:
            # No provider dispatch occurred: local reservation can settle at zero.
            span.mark_error(exc)
            span.metadata["cost_status"] = "known"
            self._finalize_span(span, run)
            raise
        if span.degraded:
            kwargs["model"] = span.model
        self._event_bus.emit("core.span_admitted", span=span)

        try:
            response = original_fn(client, *args, **kwargs)

            if is_stream:
                # Wrap the stream to capture usage when it finishes
                return _StreamWrapper(response, span, run, self)

            if provider == "anthropic":
                span.complete_from_anthropic(response)
            else:
                span.complete_from_openai(response)
        except BaseException as e:
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
        from reins.control.instrumentation import enrich

        enrich(span)
        span._prices = self._config.prices if self._config else {}
        counter = self._config.token_counter if self._config else None
        if counter:

            def bound_for(model):
                value = counter(provider, model, {**kwargs, "model": model})
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("token_counter must return a nonnegative integer")
                return value

            span.estimated_input_tokens = bound_for(span.model)
            span._bound_counter = bound_for
            span.metadata["input_bound_verified"] = True
        enforced = (
            span.metadata.get("mode", self._config.mode if self._config else "observe") == "enforce"
        )
        if enforced:
            from reins.budget.engine import CostBoundError

            sdk_client = getattr(client, "_client", None)
            if sdk_client is not None and getattr(sdk_client, "max_retries", 0) != 0:
                raise CostBoundError(
                    "Disable hidden SDK retries (max_retries=0); trace retries explicitly"
                )

            def unsupported(value):
                if isinstance(value, dict):
                    if "cache_control" in value or value.get("type") in {
                        "image",
                        "image_url",
                        "input_audio",
                        "audio",
                        "web_search",
                        "web_search_preview",
                    }:
                        return True
                    return any(unsupported(v) for v in value.values())
                return isinstance(value, list) and any(unsupported(v) for v in value)

            if unsupported(kwargs) or kwargs.get("n", 1) != 1:
                raise CostBoundError(
                    "Enforce v1 supports one text completion "
                    "without explicit cache writes/hosted tools"
                )
        if provider in {"openai", "google"} and is_stream:
            kwargs["stream_options"] = {**kwargs.get("stream_options", {}), "include_usage": True}

        self._event_bus.emit("core.span_start", span=span)
        for module in self._modules:
            try:
                span = module.on_span_start(span)
            except Exception as exc:
                span.mark_error(exc)
                span.metadata["budget_decision"] = "rejected"
                self._finalize_span(span, run)
                raise

        if span.degraded:
            kwargs["model"] = span.model
        from reins.control.instrumentation import admit

        try:
            admit(span, self._config)
        except Exception as exc:
            # No provider dispatch occurred: local reservation can settle at zero.
            span.mark_error(exc)
            span.metadata["cost_status"] = "known"
            self._finalize_span(span, run)
            raise
        if span.degraded:
            kwargs["model"] = span.model
        self._event_bus.emit("core.span_admitted", span=span)

        try:
            response = await original_fn(client, *args, **kwargs)

            if is_stream:
                return _AsyncStreamWrapper(response, span, run, self)

            if provider == "anthropic":
                span.complete_from_anthropic(response)
            else:
                span.complete_from_openai(response)
        except BaseException as e:
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
        from reins.core.monitor import current_step

        span.parent_span_id = current_step.get()
        span.run_id = run.run_id
        span.metadata.update(run.metadata)
        span.metadata["agent_name"] = run.agent_name
        # Propagate on_exceed strategy so Budget engine can read it
        if "on_exceed" in run.metadata:
            span.metadata["on_exceed"] = run.metadata["on_exceed"]
        # Propagate budget limit for run-level budget enforcement
        if run.budget_limit is not None:
            span.metadata["budget_limit"] = str(run.budget_limit)

    def _finalize_span(self, span: SpanData, run: Any) -> None:
        """Post-hooks + persist, once even for closed/cancelled streams."""
        if getattr(span, "_recorded", False):
            return
        span._recorded = True
        from reins.control.instrumentation import settle

        settle(span)
        for module in self._modules:
            try:
                module.on_span_end(span)
            except Exception:
                logger.debug("Module on_span_end error", exc_info=True)

        self._event_bus.emit("core.span_end", span=span)

        if run and not span.metadata.get("adapter_observation"):
            run.add_span_cost(span)

        try:
            self._storage.insert_span(span)
        except Exception:
            logger.debug("Failed to persist span", exc_info=True)


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
        self._usage_complete = False

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
        except BaseException as exc:
            self._span.mark_error(exc)
            self._span.metadata["incomplete_stream"] = True
            self._finalize()
            raise

    def close(self):
        try:
            if hasattr(self._stream, "close"):
                return self._stream.close()
        finally:
            self._span.metadata["incomplete_stream"] = True
            self._finalize()

    def __enter__(self):
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        if not self._finalized:
            self._span.metadata["incomplete_stream"] = True
        self._finalize()
        if hasattr(self._stream, "__exit__"):
            return self._stream.__exit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def _extract_usage(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        usage = getattr(event, "usage", None)
        if usage and (kind in {"message_delta", "message_stop"} or hasattr(event, "choices")):
            self._usage_complete = True
        """Extract usage from streaming events (Anthropic message_delta / OpenAI chunk)."""
        # Anthropic: event.type == "message_delta", event.usage
        if hasattr(event, "usage") and event.usage:
            usage = event.usage
            if self._span._special_usage(usage):
                self._span.metadata["incomplete_stream"] = True
            if getattr(usage, "input_tokens", None) is not None:
                self._usage["input_tokens"] = getattr(usage, "input_tokens", 0)
            if getattr(usage, "output_tokens", None) is not None:
                self._usage["output_tokens"] = getattr(usage, "output_tokens", 0)

        # Anthropic MessageStream final message
        if hasattr(event, "message") and hasattr(event.message, "usage"):
            u = event.message.usage
            if self._span._special_usage(u):
                self._span.metadata["incomplete_stream"] = True
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
        if (
            self._usage_complete
            and self._usage
            and not self._span.metadata.get("incomplete_stream")
        ):
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
        self._usage_complete = False

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
        except BaseException as exc:
            self._span.mark_error(exc)
            self._span.metadata["incomplete_stream"] = True
            self._finalize()
            raise

    async def aclose(self):
        try:
            if hasattr(self._stream, "aclose"):
                await self._stream.aclose()
            elif hasattr(self._stream, "close"):
                await self._stream.close()
        finally:
            self._span.metadata["incomplete_stream"] = True
            self._finalize()

    async def __aenter__(self):
        if hasattr(self._stream, "__aenter__"):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        if not self._finalized:
            self._span.metadata["incomplete_stream"] = True
        self._finalize()
        if hasattr(self._stream, "__aexit__"):
            return await self._stream.__aexit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def _extract_usage(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        usage = getattr(event, "usage", None)
        if usage and (kind in {"message_delta", "message_stop"} or hasattr(event, "choices")):
            self._usage_complete = True
        """Same logic as sync version."""
        if hasattr(event, "usage") and event.usage:
            usage = event.usage
            if self._span._special_usage(usage):
                self._span.metadata["incomplete_stream"] = True
            if getattr(usage, "input_tokens", None) is not None:
                self._usage["input_tokens"] = getattr(usage, "input_tokens", 0)
            if getattr(usage, "output_tokens", None) is not None:
                self._usage["output_tokens"] = getattr(usage, "output_tokens", 0)

        if hasattr(event, "message") and hasattr(event.message, "usage"):
            u = event.message.usage
            if self._span._special_usage(u):
                self._span.metadata["incomplete_stream"] = True
            self._usage["input_tokens"] = getattr(u, "input_tokens", 0)
            self._usage["output_tokens"] = getattr(u, "output_tokens", 0)

        if hasattr(event, "choices") and hasattr(event, "usage") and event.usage:
            self._usage["prompt_tokens"] = getattr(event.usage, "prompt_tokens", 0)
            self._usage["completion_tokens"] = getattr(event.usage, "completion_tokens", 0)

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if (
            self._usage_complete
            and self._usage
            and not self._span.metadata.get("incomplete_stream")
        ):
            self._span.complete_from_usage_dict(self._usage)
        else:
            self._span.ended_at = __import__("reins.core.models", fromlist=["_utcnow"])._utcnow()
        self._instrumentor._finalize_span(self._span, self._run)
