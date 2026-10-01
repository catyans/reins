"""Tests for framework adapters."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# === LangChain Adapter Tests ===


class TestLangChainAdapter:
    """Tests for ReinsCallbackHandler."""

    def _make_handler(self):
        from reins.adapters.langchain import ReinsCallbackHandler

        return ReinsCallbackHandler(agent_name="test_lc")

    def _mock_runtime(self, storage):
        """Patch the runtime to use test storage."""
        runtime = SimpleNamespace(
            modules=[],
            storage=storage,
            event_bus=MagicMock(),
        )
        return patch("reins.adapters.base._get_runtime", return_value=runtime)

    def test_on_llm_start_creates_span(self, storage):
        handler = self._make_handler()
        run_id = uuid.uuid4()

        with self._mock_runtime(storage):
            handler.on_llm_start(
                serialized={"id": ["langchain", "chat_models", "ChatAnthropic"]},
                prompts=["Hello, tell me about Python"],
                run_id=run_id,
                metadata={"ls_model_name": "claude-sonnet-4"},
            )

        assert str(run_id) in handler._spans
        span = handler._spans[str(run_id)]
        assert span.provider == "anthropic"
        assert span.model == "claude-sonnet-4"
        assert span.metadata["agent_name"] == "test_lc"

    def test_on_llm_end_finalizes_span(self, storage):
        handler = self._make_handler()
        run_id = uuid.uuid4()

        with self._mock_runtime(storage):
            handler.on_llm_start(
                serialized={"id": ["langchain", "ChatOpenAI"]},
                prompts=["Hi"],
                run_id=run_id,
                metadata={"ls_model_name": "gpt-4o"},
            )

            response = SimpleNamespace(
                llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 20}},
                generations=[],
            )
            handler.on_llm_end(response=response, run_id=run_id)

        # Span should be removed from tracking
        assert str(run_id) not in handler._spans

        # Should be in storage
        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert rows[0]["tokens_in"] == 10
        assert rows[0]["tokens_out"] == 20

    def test_on_tool_start_end(self, storage):
        handler = self._make_handler()
        run_id = uuid.uuid4()

        with self._mock_runtime(storage):
            handler.on_tool_start(
                serialized={"name": "web_search"},
                input_str="search query",
                run_id=run_id,
            )
            handler.on_tool_end(output="search results", run_id=run_id)

        rows = storage.query("SELECT * FROM spans WHERE span_type = 'tool'")
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "web_search"
        assert rows[0]["tool_status"] == "success"

    def test_on_llm_error(self, storage):
        handler = self._make_handler()
        run_id = uuid.uuid4()

        with self._mock_runtime(storage):
            handler.on_llm_start(
                serialized={"id": ["ChatAnthropic"]},
                prompts=["Hi"],
                run_id=run_id,
                metadata={},
            )
            handler.on_llm_error(error=ValueError("API timeout"), run_id=run_id)

        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert rows[0]["status"] == "error"
        assert "API timeout" in rows[0]["error_message"]

    def test_provider_detection(self):
        from reins.adapters.langchain import _guess_provider

        assert (
            _guess_provider({"id": ["langchain", "ChatAnthropic"]}, "claude-sonnet-4")
            == "anthropic"
        )
        assert _guess_provider({"id": ["langchain", "ChatOpenAI"]}, "gpt-4o") == "openai"
        assert (
            _guess_provider({"id": ["langchain", "ChatGoogleGenerativeAI"]}, "gemini-2.5-pro")
            == "google"
        )
        assert _guess_provider({"id": []}, "unknown-model") == "unknown"


# === OpenAI Agents SDK Adapter Tests ===


class TestOpenAIAgentsAdapter:
    """Tests for ReinsTracingProcessor."""

    def _make_processor(self):
        from reins.adapters.openai_agents import ReinsTracingProcessor

        return ReinsTracingProcessor(agent_name="test_oai")

    def _mock_runtime(self, storage):
        runtime = SimpleNamespace(
            modules=[],
            storage=storage,
            event_bus=MagicMock(),
        )
        return patch("reins.adapters.base._get_runtime", return_value=runtime)

    def test_llm_span(self, storage):
        proc = self._make_processor()

        span = SimpleNamespace(
            span_id="span-1",
            name="generation",
            span_data=SimpleNamespace(
                model="gpt-4o",
                usage=None,
            ),
        )

        with self._mock_runtime(storage):
            proc.on_trace_start(SimpleNamespace(trace_id="trace-1"))
            proc.on_span_start(span)

            # Update span_data with usage before end
            span.span_data.usage = SimpleNamespace(input_tokens=100, output_tokens=50)
            span.error = None
            proc.on_span_end(span)

        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert rows[0]["provider"] == "openai"
        assert rows[0]["tokens_in"] == 100
        assert rows[0]["tokens_out"] == 50

    def test_tool_span(self, storage):
        proc = self._make_processor()

        span = SimpleNamespace(
            span_id="span-2",
            name="tool_call",
            span_data=SimpleNamespace(
                name="web_search",
                input="query",
                output="results",
                error=None,
            ),
            error=None,
        )

        with self._mock_runtime(storage):
            proc.on_span_start(span)
            proc.on_span_end(span)

        rows = storage.query("SELECT * FROM spans WHERE span_type = 'tool'")
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "web_search"
        assert rows[0]["tool_status"] == "success"

    def test_handoff_span(self, storage):
        proc = self._make_processor()

        span = SimpleNamespace(
            span_id="span-3",
            name="handoff",
            span_data=SimpleNamespace(
                from_agent="researcher",
                to_agent="writer",
            ),
            error=None,
        )

        with self._mock_runtime(storage):
            proc.on_span_start(span)
            proc.on_span_end(span)

        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert "handoff" in rows[0]["name"]


# === CrewAI Adapter Tests ===


class TestCrewAIAdapter:
    """Tests for CrewAI callbacks."""

    def _mock_runtime(self, storage):
        runtime = SimpleNamespace(
            modules=[],
            storage=storage,
            event_bus=MagicMock(),
        )
        return patch("reins.adapters.base._get_runtime", return_value=runtime)

    def test_step_callback_tool(self, storage):
        from reins.adapters.crewai import reins_step_callback

        step = SimpleNamespace(
            tool="web_search",
            tool_input="query",
            text="search results here",
            agent="researcher",
        )

        with self._mock_runtime(storage):
            reins_step_callback(step)

        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert rows[0]["span_type"] == "tool"
        assert rows[0]["tool_name"] == "web_search"

    def test_task_callback(self, storage):
        from reins.adapters.crewai import reins_task_callback

        output = SimpleNamespace(
            description="Research AI trends",
            agent="researcher",
            raw="Here are the trends..." * 100,
        )

        with self._mock_runtime(storage):
            reins_task_callback(output)

        rows = storage.query("SELECT * FROM spans WHERE span_type = 'custom'")
        assert len(rows) == 1
        assert "Research AI" in rows[0]["name"]

    def test_instrument_crew(self, storage):
        from reins.adapters.crewai import instrument_crew

        agent1 = SimpleNamespace(step_callback=None)
        agent2 = SimpleNamespace(step_callback=None)
        task1 = SimpleNamespace(callback=None)

        crew = SimpleNamespace(agents=[agent1, agent2], tasks=[task1])

        with self._mock_runtime(storage):
            instrument_crew(crew)

        assert agent1.step_callback is not None
        assert agent2.step_callback is not None
        assert task1.callback is not None


# === OTel Adapter Tests ===


class TestOTelAdapter:
    """Tests for ReinsSpanExporter."""

    def _make_exporter(self):
        from reins.adapters.otel import ReinsSpanExporter

        return ReinsSpanExporter(agent_name="test_otel")

    def _mock_runtime(self, storage):
        runtime = SimpleNamespace(
            modules=[],
            storage=storage,
            event_bus=MagicMock(),
        )
        return patch("reins.adapters.base._get_runtime", return_value=runtime)

    def _make_otel_span(self, **overrides):
        """Create a fake OTel ReadableSpan."""
        defaults = {
            "name": "test_span",
            "attributes": {
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-sonnet-4",
                "gen_ai.response.model": "claude-sonnet-4",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
            },
            "context": SimpleNamespace(span_id=12345, trace_id=67890),
            "parent": None,
            "start_time": 1000000000,  # 1 second in nanoseconds
            "end_time": 2000000000,  # 2 seconds
            "status": SimpleNamespace(status_code=1, description=""),  # OK
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_export_genai_span(self, storage):
        exporter = self._make_exporter()
        otel_span = self._make_otel_span()

        with self._mock_runtime(storage):
            result = exporter.export([otel_span])

        assert result == 0  # SUCCESS
        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert rows[0]["provider"] == "anthropic"
        assert rows[0]["model"] == "claude-sonnet-4"
        assert rows[0]["tokens_in"] == 100
        assert rows[0]["tokens_out"] == 50
        assert rows[0]["cost"] > 0

    def test_export_non_genai_span(self, storage):
        exporter = self._make_exporter()
        otel_span = self._make_otel_span(
            name="http.request",
            attributes={"http.method": "GET"},
        )

        with self._mock_runtime(storage):
            result = exporter.export([otel_span])

        assert result == 0
        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
        assert rows[0]["span_type"] == "custom"

    def test_export_error_span(self, storage):
        exporter = self._make_exporter()
        otel_span = self._make_otel_span(
            status=SimpleNamespace(status_code=2, description="API error"),
        )

        with self._mock_runtime(storage):
            exporter.export([otel_span])

        rows = storage.query("SELECT * FROM spans")
        assert rows[0]["status"] == "error"
        assert rows[0]["error_message"] == "API error"

    def test_export_multiple_spans(self, storage):
        exporter = self._make_exporter()
        spans = [
            self._make_otel_span(
                name=f"span_{i}",
                context=SimpleNamespace(span_id=10000 + i, trace_id=67890),
            )
            for i in range(3)
        ]

        with self._mock_runtime(storage):
            result = exporter.export(spans)

        assert result == 0
        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 3

    def test_processor(self, storage):
        from reins.adapters.otel import ReinsSpanProcessor

        processor = ReinsSpanProcessor(agent_name="test_proc")
        otel_span = self._make_otel_span()

        with self._mock_runtime(storage):
            processor.on_start(otel_span)
            processor.on_end(otel_span)

        rows = storage.query("SELECT * FROM spans")
        assert len(rows) == 1
