# Changelog

All notable changes to Reins are documented here.

## [0.1.0] - 2026-04-03

### Added

- **Core**: Auto-instrumentation for Anthropic and OpenAI SDKs
- **Core**: DuckDB embedded storage (zero infrastructure)
- **Core**: Plugin module system with entry_points discovery and event bus
- **Core**: `@trace()` decorator and `wrap()` client wrapper
- **Core**: Streaming response support (sync and async)
- **Budget**: Atomic reservation engine with 4 exceed strategies (degrade/pause/alert/reject)
- **Budget**: Model degradation chains (opus→sonnet→haiku, o3→gpt-4o→mini)
- **Budget**: Circuit breaker for runaway agent loops
- **Budget**: Balance persistence to DuckDB with daily/monthly auto-reset
- **Budget**: Cost estimation from actual prompt token count
- **Budget**: YAML configuration for team/agent budgets
- **Proxy**: Transparent reverse proxy for Claude Code (`ANTHROPIC_BASE_URL`)
- **Trace**: `reins trace list` — recent runs table
- **Trace**: `reins trace show` — colored terminal call tree
- **Trace**: `reins trace export --format otel` — OTLP JSON export
- **Lens**: `reins replay` — interactive step-by-step agent replay
- **Lens**: `reins health` — context health curve with 3-metric scoring
- **Adapters**: LangChain/LangGraph `ReinsCallbackHandler`
- **Adapters**: OpenAI Agents SDK `ReinsTracingProcessor`
- **Adapters**: CrewAI `instrument_crew()` helper
- **Adapters**: Generic OTel `ReinsSpanExporter` (covers Semantic Kernel, Pydantic AI, Haystack, Mastra)
- **CLI**: `reins report`, `reins query`, `reins proxy`, `reins config`

### Security

- Parameterized SQL queries in all CLI commands (no SQL injection)
- API keys never logged or stored
- DuckDB file permissions restricted to current user
