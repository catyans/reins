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
        span = SpanData.from_llm_call(provider, kwargs)
        run = get_current_run()
        if run:
            span.run_id = run.run_id
            span.metadata["agent_name"] = run.agent_name

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
        span = SpanData.from_llm_call(provider, kwargs)
        run = get_current_run()
        if run:
            span.run_id = run.run_id
            span.metadata["agent_name"] = run.agent_name

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
