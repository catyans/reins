"""LangChain / LangGraph adapter.

Usage:
    from reins.adapters.langchain import ReinsCallbackHandler

    handler = ReinsCallbackHandler()

    # With LangChain
    llm = ChatAnthropic(callbacks=[handler])

    # Or globally
    chain.invoke({"input": "..."}, config={"callbacks": [handler]})

    # With LangGraph
    app.invoke({"messages": [...]}, config={"callbacks": [handler]})
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from reins.adapters.base import finalize_span, process_span
from reins.core.models import SpanData

logger = logging.getLogger("reins.adapters.langchain")


class ReinsCallbackHandler:
    """LangChain BaseCallbackHandler compatible callback for Reins tracing + budget.

    Implements the callback protocol without importing langchain, so it works
    even if langchain is not installed (duck-typing).
    """

    def __init__(self, agent_name: str = "langchain") -> None:
        self.agent_name = agent_name
        self._spans: dict[str, SpanData] = {}  # run_id -> span

    # --- LLM callbacks ---

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        """Extract chat roles without importing LangChain or persisting prompt text."""
        components = {"system": [], "history": [], "user": []}
        for conversation in messages:
            for index, message in enumerate(conversation):
                role = getattr(message, "type", "human")
                content = str(getattr(message, "content", ""))
                group = (
                    "system"
                    if role == "system"
                    else (
                        "user" if index == len(conversation) - 1 and role == "human" else "history"
                    )
                )
                components[group].append(content)
        context = {k: "\n".join(v) for k, v in components.items()}
        context.update((metadata or {}).get("reins_context", {}))
        self.on_llm_start(
            serialized,
            [],
            run_id=run_id,
            metadata={**(metadata or {}), "reins_context": context},
            **kwargs,
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM starts generating."""
        model = (metadata or {}).get("ls_model_name", "") or serialized.get("id", [""])[-1]
        provider = _guess_provider(serialized, model)

        # Estimate input tokens from prompt length
        total_chars = sum(len(p) for p in prompts)
        estimated_in = max(total_chars // 4, 50)

        span = SpanData(
            span_type="llm",
            name=f"{provider}.chat.create",
            provider=provider,
            model=model,
            model_requested=model,
            estimated_input_tokens=estimated_in,
            estimated_max_output_tokens=kwargs.get("invocation_params", {}).get("max_tokens", 1024),
        )
        from reins.control.usage import estimate_components

        supplied = (metadata or {}).get("reins_context", {})
        span.metadata["context_estimates"] = estimate_components(
            user="\n".join(prompts) if not supplied else supplied.get("user", ""),
            system=supplied.get("system", ""),
            history=supplied.get("history", ""),
            retrieval=supplied.get("retrieval", ""),
            tools=supplied.get("tools", kwargs.get("invocation_params", {}).get("tools", [])),
        )
        span.metadata["context_revision"] = (metadata or {}).get("reins_revision", "unversioned")
        span.metadata["agent_name"] = self.agent_name
        if parent_run_id:
            span.parent_span_id = str(parent_run_id)

        # Pre-hooks (budget check, possible degradation)
        try:
            span = process_span(span)
        except Exception:
            logger.debug("Budget check failed", exc_info=True)
            raise

        self._spans[str(run_id)] = span

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Called when LLM finishes generating."""
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return

        # Extract usage from LangChain LLMResult
        tokens_in = 0
        tokens_out = 0
        if hasattr(response, "llm_output") and response.llm_output:
            usage = response.llm_output.get("token_usage", {})
            tokens_in = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)

        # Also check generations for usage info
        if not tokens_in and hasattr(response, "generations"):
            for gen_list in response.generations:
                for gen in gen_list:
                    info = getattr(gen, "generation_info", {}) or {}
                    usage = info.get("usage", {})
                    tokens_in += usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                    tokens_out += usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)

        from reins.control.client import active_workflow

        current = active_workflow()
        if current:
            try:
                current.client.post(
                    "context/sample",
                    {
                        "task_id": current.context["task_id"],
                        "sample_id": str(run_id),
                        "revision": span.metadata["context_revision"],
                        "components": span.metadata["context_estimates"],
                        "provider_input_tokens": tokens_in if tokens_in else None,
                    },
                )
            except Exception:
                logger.warning("Context observation unavailable; billing is unaffected")
        span.complete(tokens_in=tokens_in, tokens_out=tokens_out)
        finalize_span(span)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Called when LLM errors."""
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return
        span.mark_error(error)
        finalize_span(span)

    # --- Tool callbacks ---

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool starts."""
        tool_name = serialized.get("name", "unknown_tool")
        span = SpanData.from_tool_call(tool_name)
        span.metadata["agent_name"] = self.agent_name
        if parent_run_id:
            span.parent_span_id = str(parent_run_id)
        self._spans[str(run_id)] = span

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Called when a tool finishes."""
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return
        from reins.core.models import _utcnow

        span.ended_at = _utcnow()
        span.duration_ms = (span.ended_at - span.started_at).total_seconds() * 1000
        span.tool_status = "success"
        finalize_span(span)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Called when a tool errors."""
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return
        span.mark_error(error)
        span.tool_status = "error"
        span.tool_error = str(error)
        finalize_span(span)

    # --- Chain callbacks (for LangGraph nodes) ---

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when a chain/graph node starts."""
        name = serialized.get("id", ["chain"])[-1]
        span = SpanData(
            span_type="custom",
            name=f"chain.{name}",
        )
        span.metadata["agent_name"] = self.agent_name
        if parent_run_id:
            span.parent_span_id = str(parent_run_id)
        self._spans[str(run_id)] = span

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Called when a chain/graph node finishes."""
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return
        from reins.core.models import _utcnow

        span.ended_at = _utcnow()
        span.duration_ms = (span.ended_at - span.started_at).total_seconds() * 1000
        finalize_span(span)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        span = self._spans.pop(str(run_id), None)
        if span is None:
            return
        span.mark_error(error)
        finalize_span(span)


def _guess_provider(serialized: dict, model: str) -> str:
    """Guess the LLM provider from serialized data or model name."""
    id_parts = serialized.get("id", [])
    id_str = ".".join(id_parts).lower()

    if "anthropic" in id_str or "claude" in model.lower():
        return "anthropic"
    if "openai" in id_str or "gpt" in model.lower() or model.startswith("o"):
        return "openai"
    if "google" in id_str or "gemini" in model.lower():
        return "google"
    return "unknown"
