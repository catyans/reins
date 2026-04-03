# Reins User Guide

> Complete guide to using Reins for AI agent cost governance, tracing, and debugging.

---

## Table of Contents

1. [Installation](#installation)
2. [Core Concepts](#core-concepts)
3. [Budget Control](#budget-control)
4. [Tracing & Visualization](#tracing--visualization)
5. [Context Health & Debugging](#context-health--debugging)
6. [Proxy Mode (Claude Code)](#proxy-mode-claude-code)
7. [Framework Adapters](#framework-adapters)
8. [Configuration Reference](#configuration-reference)
9. [CLI Reference](#cli-reference)

---

## Installation

```bash
# Core only (tracing + DuckDB storage)
pip install reins

# With budget control
pip install reins[budget]

# With debugging tools
pip install reins[lens]

# With proxy for Claude Code
pip install reins[proxy]

# Everything
pip install reins[all]
```

Reins requires **Python 3.9+** and has no infrastructure dependencies — everything runs locally with embedded DuckDB.

---

## Core Concepts

### The `@trace` Decorator

The `@trace` decorator is the primary API. It:
1. Creates a **Run** (one complete agent execution)
2. Auto-instruments all LLM calls within the function
3. Records **Spans** (individual LLM/tool calls) to DuckDB
4. Enforces budget limits (if configured)

```python
from reins import trace

# Basic tracing (no budget)
@trace
async def my_agent(task):
    response = await client.messages.create(model="claude-sonnet-4-20250514", ...)
    return response

# With budget control
@trace(budget="$0.50", on_exceed="degrade", agent_name="researcher")
async def research_agent(task):
    ...
```

### Data Model

```
Run (one agent execution)
├── Span (LLM call: model, tokens, cost, latency)
├── Span (tool call: name, status, duration)
├── Span (LLM call: possibly degraded model)
└── ...
```

All data is stored in `~/.reins/data/traces.duckdb`.

### Plugin Modules

| Module | What it does | Install |
|--------|-------------|---------|
| **Core** | Auto-instrumentation, storage, events | Always included |
| **Budget** | Cost limits, degradation, circuit breaker | `reins[budget]` |
| **Trace** | Visualization, OTel export | `reins[trace]` |
| **Lens** | Replay, context health, root cause | `reins[lens]` |
| **Pulse** | Guardrails, evaluation, regression tests | `reins[pulse]` |

Modules auto-discover each other via Python `entry_points` and communicate through an event bus. Install only what you need.

---

## Budget Control

### Per-Run Budget

```python
@trace(budget="$0.50", on_exceed="degrade")
async def my_agent(task):
    # If total cost approaches $0.50, Reins auto-switches
    # claude-sonnet → claude-haiku (cheaper but still works)
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": task}],
    )
    return response
```

### Exceed Strategies

| Strategy | Behavior |
|----------|----------|
| `degrade` | Auto-switch to cheaper model (recommended) |
| `alert` | Log warning, continue execution |
| `pause` | Raise `BudgetPausedError` (for human-in-the-loop) |
| `reject` | Raise `BudgetExceededError` (hard stop) |

### Degradation Chains

```
Anthropic: opus → sonnet → haiku
OpenAI:    o3 → gpt-4o → gpt-4o-mini
Google:    gemini-2.5-pro → gemini-2.5-flash → gemini-2.0-flash
```

### YAML Configuration

```yaml
# reins.yaml (in project root or ~/.reins/config.yaml)
budgets:
  daily: $50.00          # Organization daily limit
  agents:
    research_agent:
      per_run: $2.00
      daily: $20.00
      on_exceed: degrade
    code_agent:
      per_run: $0.50
      on_exceed: reject
    claude_code:
      daily: $10.00
      on_exceed: degrade

modules:
  budget:
    enabled: true
  trace:
    enabled: true
    retention: 30d
  lens:
    enabled: true
  pulse:
    enabled: false
```

### Circuit Breaker

Reins automatically detects agent loops (>30 LLM calls per minute for the same agent) and halts them:

```
BudgetExceededError: Budget exceeded for agent 'research_agent'
  Circuit breaker tripped: 34 calls in 60 seconds
```

### Budget Persistence

Budgets survive process restarts. Daily budgets auto-reset at midnight UTC. Balance is stored in `~/.reins/data/traces.duckdb` in the `budget_balances` table.

---

## Tracing & Visualization

### List Recent Runs

```bash
$ reins trace list

  Recent Agent Runs
  ────────────────────────────────────────────────────
  Run ID       Agent              Status       Cost     Spans  Degraded
  ────────────────────────────────────────────────────
  a1b2c3d4     research_agent     completed    $0.4521      8         2
  e5f6g7h8     code_agent         failed       $0.0312      3         0
```

### View Call Tree

```bash
$ reins trace show a1b2

  Run: a1b2c3d4
  Agent: research_agent  |  Status: completed  |  Cost: $0.4521
  ⚠ 2 call(s) were auto-degraded
  ────────────────────────────────────────────────────

  └── 🤖 anthropic.chat.create  claude-sonnet-4  1200→450 tok  1.2s  $0.0285
  └── 🔧 tool.web_search  web_search  success  340ms
  └── 🤖 anthropic.chat.create  claude-sonnet-4→claude-haiku-4 (degraded)  800→200 tok  0.5s  $0.0024
  └── ...
```

### Export to Grafana/Datadog

```bash
# OTLP JSON (OpenTelemetry GenAI semantic conventions)
reins trace export --format otel -o traces.json

# Plain JSON
reins trace export --format json --run-id a1b2
```

### SQL Queries

```bash
# Most expensive agents
reins query "SELECT agent_name, SUM(cost) as total FROM spans GROUP BY agent_name ORDER BY total DESC"

# Degradation frequency
reins query "SELECT model_requested, model, COUNT(*) FROM spans WHERE degraded = true GROUP BY 1, 2"
```

---

## Context Health & Debugging

### Context Health Analysis

```bash
$ reins health a1b2

  Context Health Report
  Agent: research_agent  |  Run: a1b2c3d4
  ──────────────────────────────────────────────────

  Health Curve  (1.0 = healthy, 0.0 = degraded)

  1.0│████████████████████████████
  0.5│                            ████████████
     │                                        ██████
     └──────────────────────────────────────────────
      1                                            12

  Per-Span Breakdown
  ──────────────────────────────────────────────────
    # Model                     Tokens     Util  Effic   Dup  Health
    1 claude-sonnet-4           1200→450   0.6%  1.00  1.00   0.970
    2 claude-sonnet-4           3400→300   1.7%  0.85  1.00   0.915
   ...
   12 claude-haiku-4           45000→120  22.5%  0.42  0.60   0.412
  ──────────────────────────────────────────────────
  ⚠ Context rot detected starting at span #9
```

The health score is computed from 3 metrics:
- **Utilization** (50%): How full is the context window
- **Efficiency** (30%): Output/input token ratio trend
- **Duplication** (20%): Repeated similar input sizes (loop signal)

### Agent Replay

```bash
$ reins replay a1b2

  🎬 Agent Replay
  Agent: research_agent  |  Run: a1b2c3d4
  [Enter] next step  [a] auto-play  [q] quit

  [1/12] 🤖 anthropic.chat.create
         Model: claude-sonnet-4
         Tokens: 1200 in → 450 out
         Cost: $0.028500  (cumulative: $0.0285)

  [2/12] 🔧 tool.web_search
         Tool: web_search
         Result: success
         Duration: 340ms

  ...
```

---

## Proxy Mode (Claude Code)

### Setup

```bash
# Install proxy dependencies
pip install reins[proxy]

# Start proxy
reins proxy --port 8082 --budget '$5/day' --on-exceed degrade
```

### Connect Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8082
claude "help me refactor this code"
```

Reins sits between Claude Code and the Anthropic API:

```
Claude Code → Reins Proxy → Anthropic API
                  ↓
            Budget check
            Cost recording
            Auto-degradation
            DuckDB storage
```

### Works With Any Tool

Any tool that respects `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL`:
- Claude Code
- aider
- Cursor
- Custom scripts using Anthropic/OpenAI SDKs

---

## Framework Adapters

### LangChain / LangGraph

```python
from reins.adapters.langchain import ReinsCallbackHandler

handler = ReinsCallbackHandler(agent_name="my_lc_agent")

# On LLM
llm = ChatAnthropic(callbacks=[handler])

# On chain
chain.invoke({"input": "..."}, config={"callbacks": [handler]})

# On LangGraph
app.invoke(state, config={"callbacks": [handler]})
```

Captures: LLM calls (with token usage), tool calls, chain/graph node execution.

### OpenAI Agents SDK

```python
from reins.adapters.openai_agents import ReinsTracingProcessor
from agents import add_trace_processor

add_trace_processor(ReinsTracingProcessor(agent_name="my_oai_agent"))
```

Captures: Generation spans, tool calls, agent handoffs.

### CrewAI

```python
from reins.adapters.crewai import instrument_crew

crew = Crew(agents=[researcher, writer], tasks=[research_task, write_task])
instrument_crew(crew)
result = crew.kickoff()
```

Captures: Per-agent steps, task completions, tool usage.

### OTel-Native Frameworks

Works with Semantic Kernel, Pydantic AI, Haystack, Mastra, or any OTel-instrumented code:

```python
from reins.adapters.otel import ReinsSpanProcessor
from opentelemetry.sdk.trace import TracerProvider

provider = TracerProvider()
provider.add_span_processor(ReinsSpanProcessor())
```

Reads GenAI semantic conventions (`gen_ai.system`, `gen_ai.usage.*`) automatically.

---

## Configuration Reference

### reins.yaml

```yaml
# Storage
storage: duckdb                    # duckdb (default) | clickhouse (future)
storage_path: ~/.reins/data/traces.duckdb
retention_days: 30

# Budgets
budgets:
  daily: $50.00
  monthly: $500.00
  on_exceed: alert                 # Default strategy
  agents:
    <agent_name>:
      per_run: $1.00
      daily: $20.00
      monthly: $200.00
      on_exceed: degrade           # degrade | pause | alert | reject

# Modules
modules:
  budget: { enabled: true }
  trace: { enabled: true }
  lens: { enabled: true }
  pulse: { enabled: false }
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_BASE_URL` | Point to Reins proxy (e.g., `http://localhost:8082`) |
| `OPENAI_BASE_URL` | Point to Reins proxy for OpenAI traffic |

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `reins report` | Cost report (today/week/month, per-agent) |
| `reins query "<sql>"` | SQL query against trace database |
| `reins proxy` | Start transparent reverse proxy |
| `reins trace list` | List recent runs |
| `reins trace show <id>` | Show call tree for a run |
| `reins trace spans <id>` | Detailed span list |
| `reins trace export` | Export traces (JSON or OTLP) |
| `reins replay <id>` | Interactive agent replay |
| `reins health <id>` | Context health analysis |
| `reins config` | Show current configuration |
| `reins version` | Show version |

---

## Project Status

| Phase | Status | What |
|-------|--------|------|
| Phase 1 | **Complete** | Core + Budget + Proxy + CLI |
| Phase 2 | **Complete** | Trace visualization + Context Health + Replay + OTel export |
| Hardening | **Complete** | 6 PRs: cost estimation, persistence, streaming, SQL injection, tests |
| Adapters | **Complete** | LangChain, OpenAI Agents SDK, CrewAI, OTel |
| Phase 3 | Planned | Pulse (guardrails, evaluation, regression tests) |
