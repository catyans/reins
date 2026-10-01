## Unreleased — data-agent execution and audited cases

- Add isolated, deadline-aware online batching with bounded provider concurrency.
- Add SQLite step checkpoints, dependency invalidation and explicit interrupted-call reconciliation.
- Add validation-only segment policy learning, immutable bundle files and a holdout adoption gate.
- Add explicit text/cached-token Gemini cost estimates without modifying pending billing entries.
- Add four real Gemini workload experiments, adapted public Strands reflection baselines,
  recorded English case replays and disclosed deterministic parser baselines.

## Local dashboard addition (unreleased)

## Unreleased — outcome-based policy optimization

- Add paired, isolated policy experiments with business-quality and latency gates,
  persisted manifests, explicit rejection reasons and cost-per-accepted-task selection.
- Add validated primary/fallback execution; include validation and fallback costs.
- Separate validation selection from test-only reporting; export reports through
  `reins optimize`, list experiments through `reins experiments`.
- Add a read-only strategy optimization dashboard and synthetic Data Agent example.
- Complete the local product website with budget, diagnostics and strategy demos.
- Preserve existing budget/trace interfaces; exact-field evaluation now requires
  an expected key to actually exist even when its expected value is null.


- Read-only loopback dashboard: live tasks, steps, nested runs, retries, budget decisions,
  confirmed/reserved costs, and same-dataset policy comparisons.
- Public `step(name, kind="custom")` sync/async contexts; `dashboard`,
  `dashboard_port`, and `inactivity_seconds` configuration options.
- Durable schema-v3 lifecycle events, runtime heartbeat, unknown/interrupted-state
  display and transactional migrations. Existing billing reservations stay intact.
- Offline dashboard demo includes synthetic strategy comparisons and parallel tasks.

# v0.2.0 — Task-cost pilot (2026-09-29)

- Default observe mode; explicit opt-in enforcement with pinned prices and verified input bounds.
- Durable per-run/ancestor/UTC day/month reservations, explicit reconciliation and pending fees.
- Task-specific approved model replacements; no implicit fallback chain.
- Outcomes, external costs, retry labels, dataset evaluator and `reins compare`.
- Streaming cancellation handling and observation-only proxy status.
- Preserve historical v0.1 data; start a separate quota ledger. Python >=3.10.
- Correct package metadata and replace unverified competitive claims with support boundaries.

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

### Outcome-based batching and real API evaluation

- Add microbatch execution with identity alignment, per-item validation, deterministic transforms and selective fallback.
- Add paired configuration selection with quality, settled-cost, sample-size and batch latency gates.
- Recognize Google OpenAI-compatible calls and update Gemini 2.5 Flash / Flash-Lite token prices.
- Add retained real-API evaluation rounds and an English website case study with stronger baselines, complete fallback accounting and explicit latency tradeoffs.
