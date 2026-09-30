<p align="center">
  <h1 align="center">Reins</h1>
  <p align="center"><img src="docs/assets/reins-wordmark-v3.svg" alt="Reins Logo" width="400"/></p>
  <p align="center"><strong>Take Control of Your AI Agents</strong></p>
  <p align="center">
    The runtime platform that makes AI agents debuggable, affordable, and reliable.<br/>
    Existing tools show you what happened. Reins lets you <strong>control what happens next</strong>.
  </p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> &bull;
    <a href="#features">Features</a> &bull;
    <a href="#framework-support">Frameworks</a> &bull;
    <a href="#documentation">Docs</a> &bull;
    <a href="#why-reins">Why Reins</a>
  </p>
</p>

---

```python
pip install reins

from reins import trace

@trace(budget="$0.50", on_exceed="degrade")
async def my_agent(task: str):
    response = await client.messages.create(model="claude-sonnet-4-20250514", ...)
    return response
    # When budget runs low → auto-switches to claude-haiku (not crash)
```

**One decorator. Budget control + cost tracking + auto-degradation + trace recording.**

---

## Measured Gemini cases

Selected completed benchmarks, synchronized with the public case page on September 30, 2026.

| Workload | Accepted test records | Lower API cost per accepted result | Comparison baseline |
|---|---:|---:|---|
| Incremental data refresh | 300/300 | 72.0% | One-item Gemini 2.5 Flash Lite |
| Research paper collection | 300/300 | 63.5% | One-item Gemini 2.5 Flash |
| Project research | 300/300 | 49.6% | One-item Gemini 2.5 Flash |

[Interactive cases](https://47.245.114.167:8443/cases.html) ·
[Readable benchmark report](docs/assets/reins-savings-report.pdf) ·
[Recorded comparison data](docs/assets/featured-cases.json)

Real API calls on reformatted public metadata and controlled updates, selected
retrospectively from completed experiments. Acceptance checks required reference
fields; these are not customer production results. Savings include failed attempts
and fallback calls and are relative to the named baseline. Deterministic parsing
is also a baseline for these structured inputs. Paper collection is an offline
workload (p95 79.04 seconds); the other two examples have p95 below 60 seconds.
These results do not imply that every workflow benefits or that savings are exclusive
to Reins. The report includes the full four-workload presentation, including invoices.

The latest local raw-document project-update experiment is separate from these
benchmarks and has not established production-quality savings. Local experimental
SDK features are not part of this documentation-only update.

---

## Why Reins?

### Enterprise LLM Spending is Exploding — But Cost Controls Haven't Kept Up

Enterprise LLM API spending has surged from **$1.8B** (2023 H2) to **$8.4B** (2025 H1) — a **4.7x increase** in 18 months. Menlo Ventures projects it will hit **$15B by 2026** if current velocity holds. Total enterprise GenAI investment reached **$37B in 2025**, tripling from $11.5B in 2024.

<p align="center">
  <img src="docs/assets/llm_spend_growth.png" alt="Enterprise LLM API Spending Growth" width="700"/>
</p>

Yet the tools to govern this spending are shockingly primitive:

| Capability | Available Today? |
|---|---|
| Cost **tracking** (after the fact) | Widely available (Langfuse, LangSmith, Helicone) |
| Hard budget **limits** (block when exceeded) | Partial (LiteLLM, Portkey) |
| **Auto-degradation** (switch to cheaper model) | **No existing tool** |
| **Circuit breakers** (halt runaway loops) | **No existing tool** |
| **Per-agent** budget scoping | **No existing tool** |

<p align="center">
  <img src="docs/assets/cost_governance_gap.png" alt="Cost Governance Gap" width="750"/>
</p>

### Real Cost Overruns Are Already Happening

- **$47K LangChain Loop** (Nov 2025): Four agents in a research pipeline entered an infinite conversation loop for 11 days. The team assumed growing costs were "organic growth" — until the $47,000 bill arrived.
- **$47K Retry Storm** (Feb 2026): A data enrichment agent misinterpreted an API error code, running 2.3 million API calls over a weekend. Only the external API's rate limiter slowed it down — not the team's own controls.
- **Gartner (2025)**: Over **40% of agentic AI projects** will be canceled by 2027 due to escalating costs, unclear value, or inadequate risk controls.

### Agent Reliability is a Crisis

<p align="center">
  <img src="docs/assets/reliability_crisis.png" alt="Agent Reliability Crisis" width="700"/>
</p>

- Top SWE-bench Verified score: **79%** — but real-world performance overestimates by **up to 54%**
- Agent success rates **decline exponentially** with task duration. Claude Sonnet's "half-life" is ~59 minutes
- A survey of 306 practitioners found **reliability is the #1 barrier** to enterprise agent adoption

### Who Needs Cost Governance?

| Industry | AI Spend (2025) | Growth | Key Concern |
|----------|----------------|--------|-------------|
| **Healthcare** | ~$1.5B in vertical AI | 3.3x YoY | Compliance + cost predictability |
| **Financial Services** | 23.7% of enterprise AI market | Steady | Risk controls + audit trails |
| **Legal** | $650M market | Fast-growing | Per-case cost attribution |
| **Customer Service** | Largest agent deployment sector | Rapid | Per-conversation cost caps |

**86% of enterprises** plan to increase AI budgets in 2026 (Deloitte). The question isn't whether to spend — it's whether to spend *blindly*.

---

## Quick Start

### For Your Own Agents

```bash
pip install reins
```

```python
from reins import trace

@trace(budget="$0.50", on_exceed="degrade")
async def my_agent(task: str):
    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": task}],
    )
    return response.content[0].text
```

When the budget runs low, Reins automatically switches `claude-sonnet` to `claude-haiku` — your agent keeps running, just cheaper.

### For Claude Code

```bash
pip install reins[proxy]

# Start local proxy with $5/day budget
reins proxy --port 8082 --budget '$5/day' --on-exceed degrade

# Point Claude Code at it
export ANTHROPIC_BASE_URL=http://localhost:8082

# Use Claude Code normally — Reins controls costs transparently
claude "refactor this module"
```

### Check Your Costs

```bash
# Cost report
reins report

# Trace visualization
reins trace list
reins trace show <run_id>

# Context health analysis
reins health <run_id>

# Step-by-step replay
reins replay <run_id>
```

---

## Features

### Modular Architecture — Use What You Need

```bash
pip install reins              # Core: auto-tracing + DuckDB storage
pip install reins[budget]      # + Cost governance
pip install reins[lens]        # + Debugging (replay, context health)
pip install reins[pulse]       # + Reliability (guardrails, evaluation)
pip install reins[all]         # Everything
```

### Core (Always Installed)

- **Auto-instrumentation**: Monkey-patches Anthropic & OpenAI SDKs — zero code changes to capture all LLM calls
- **DuckDB storage**: Embedded, zero-config. No Redis, no Postgres, no Docker
- **SQL queries**: `reins query "SELECT * FROM spans WHERE cost > 0.1"`
- **Streaming support**: Transparent interception of streaming responses

### Budget Module

- **Per-run budgets**: `@trace(budget="$0.50")` — hard cap per agent execution
- **4 exceed strategies**: `degrade` (auto-switch model) / `pause` / `alert` / `reject`
- **Model degradation chains**: `opus → sonnet → haiku`, `o3 → gpt-4o → gpt-4o-mini`
- **Circuit breaker**: Auto-halts runaway agent loops (>30 calls/minute)
- **Budget persistence**: Survives process restarts, daily/monthly auto-reset
- **Cost anomaly detection**: Alerts when a run costs 3x the historical average
- **YAML team budgets**: Organization → team → agent hierarchy

```yaml
# reins.yaml
budgets:
  daily: $10.00
  agents:
    research_agent: { per_run: $2.00, on_exceed: degrade }
    code_agent: { per_run: $0.50, on_exceed: reject }
```

### Trace Module

- **`reins trace list`**: Recent runs table with cost, status, degradation count
- **`reins trace show`**: Colored terminal call tree (LLM + tool calls)
- **`reins trace export --format otel`**: OTLP JSON export for Grafana/Datadog
- **Cross-agent correlation**: Automatic trace_id propagation

### Lens Module

- **`reins replay`**: Interactive step-by-step agent replay (Enter/auto-play/quit)
- **`reins health`**: Context health curve with ASCII chart + per-span breakdown
- **Context Rot detection**: 3-metric composite score (utilization, efficiency, duplication)
- **Root cause analysis**: Automatic causal chain for failures

### Pulse Module (Phase 3)

- Runtime evaluators (coherence, instruction following)
- Guardrail engine (PII detection, SQL injection prevention)
- Automatic regression test generation from failed runs

### Proxy Mode

- **Transparent reverse proxy** for Claude Code, aider, Cursor, or any tool that respects `ANTHROPIC_BASE_URL`
- Full budget enforcement at the HTTP layer
- Zero changes to the upstream tool

---

## Framework Support

Reins integrates with **10+ agent frameworks** through 4 adapters:

| Adapter | Frameworks | Integration |
|---------|-----------|-------------|
| **`ReinsCallbackHandler`** | LangChain, LangGraph, LangFlow | `ChatAnthropic(callbacks=[handler])` |
| **`ReinsTracingProcessor`** | OpenAI Agents SDK | `add_trace_processor(processor)` |
| **`instrument_crew()`** | CrewAI | `instrument_crew(crew)` |
| **`ReinsSpanExporter`** | Semantic Kernel, Pydantic AI, Haystack, Mastra | OTel SpanProcessor |

### LangChain / LangGraph

```python
from reins.adapters.langchain import ReinsCallbackHandler

handler = ReinsCallbackHandler(agent_name="my_langchain_agent")
llm = ChatAnthropic(model="claude-sonnet-4-20250514", callbacks=[handler])
chain = prompt | llm | parser
result = chain.invoke({"input": "..."})
```

### OpenAI Agents SDK

```python
from reins.adapters.openai_agents import ReinsTracingProcessor
from agents import Agent, Runner, add_trace_processor

add_trace_processor(ReinsTracingProcessor())

agent = Agent(name="assistant", model="gpt-4o")
result = Runner.run_sync(agent, "Hello!")
```

### CrewAI

```python
from reins.adapters.crewai import instrument_crew
from crewai import Crew, Agent, Task

crew = Crew(agents=[...], tasks=[...])
instrument_crew(crew)  # One line — instruments all agents and tasks
result = crew.kickoff()
```

### Any OTel-Native Framework (Semantic Kernel, Pydantic AI, Haystack, Mastra)

```python
from reins.adapters.otel import ReinsSpanProcessor
from opentelemetry.sdk.trace import TracerProvider

provider = TracerProvider()
provider.add_span_processor(ReinsSpanProcessor())
# Now any OTel-instrumented framework is automatically traced by Reins
```

---

## How It Compares

| | **Reins** | LiteLLM | Langfuse | Helicone | Portkey |
|---|---|---|---|---|---|
| **Architecture** | SDK + Proxy | Proxy only | SDK | Proxy | Proxy/SDK |
| **Infrastructure** | Zero (embedded DuckDB) | Redis + Postgres | PostgreSQL | Cloud | Cloud |
| **Budget enforcement** | Smart degradation | Hard reject (400) | None | Rate limit | Key limit |
| **Auto model switch** | Yes | No | No | No | No |
| **Circuit breaker** | Yes | No | No | No | No |
| **Per-agent budgets** | Yes | Per-key | No | No | Partial |
| **Agent replay** | Yes | No | No | No | No |
| **Context health** | Yes | No | No | No | No |
| **Framework adapters** | 10+ frameworks | N/A | 12+ | N/A | 5+ |
| **Setup** | `pip install reins` | `docker-compose up` | `docker-compose up` | Cloud signup | Cloud signup |
| **Open source** | BSL 1.1 (→ Apache 2030) | Enterprise paywall | MIT | MIT | Proprietary |

**One-line difference**: LiteLLM is an API Gateway (needs infra, hard-rejects on exceed). Reins is an Agent Runtime (zero-infra, degrades gracefully).

---

## Architecture

<p align="center">
  <img src="docs/assets/architecture.png" alt="Reins Architecture" width="700"/>
</p>

Modules communicate via an **event bus** — install only what you need, they auto-cooperate when co-installed. For example: Lens detects context rot → notifies Budget → Budget reduces remaining allocation.

---

## Documentation

- [Product Requirements (PRD)](docs/PRD.md) — What we build and why
- [Technical Design](docs/DESIGN.md) — Architecture, data models, API design

---

## Market Context

| Metric | Value | Source |
|--------|-------|--------|
| Enterprise LLM API spend (2025 H1) | **$8.4B** | [Menlo Ventures](https://menlovc.com/perspective/2025-mid-year-llm-market-update/) |
| YoY GenAI enterprise investment growth | **3.2x** ($11.5B → $37B) | [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/) |
| Projected LLM API spend (2026) | **$15B+** | [Menlo Ventures](https://menlovc.com/perspective/2025-mid-year-llm-market-update/) |
| Agentic AI projects to be canceled by 2027 | **>40%** | [Gartner](https://www.gartner.com) |
| Agent reliability as #1 enterprise barrier | **72%** of practitioners | [Pan et al. (2025)](https://simmering.dev/blog/agent-benchmarks/) |
| Enterprises increasing AI budget in 2026 | **86%** | [Deloitte State of AI 2026](https://www.deloitte.com/us/en/what-we-do/capabilities/applied-artificial-intelligence/content/state-of-ai-in-the-enterprise.html) |
| Langfuse: SDK installs/month | **26M+** | [ClickHouse acquisition](https://langfuse.com/) |
| LangChain valuation (Oct 2025) | **$1.25B** | [Series B](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langchain-setup-tools-agents-memory/langchain-funding-valuation-2025-complete-financial-overview) |
| Healthcare vertical AI spend (2025) | **$1.5B** (3.3x YoY) | [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/) |

---

## Development

```bash
# Clone
git clone https://github.com/catyans/reins.git
cd reins

# Install with dev deps
pip install -e ".[dev,all]"

# Run tests
pytest tests/ -v

# Generate charts
python scripts/generate_charts.py
```

**99 tests** across unit and integration suites.

---

## License

[Business Source License 1.1](LICENSE) (BSL 1.1)

- **Free for**: personal use, internal enterprise use, academic research, contributing back
- **Not allowed**: building a competing commercial AI agent cost governance product/service
- **Auto-converts to Apache 2.0** on April 4, 2030

For commercial licensing inquiries: 237344440@qq.com

---

## Author

**Yanshu Wang** ([@catyans](https://github.com/catyans)) — [https://catyans.github.io](https://catyans.github.io)

---

<p align="center">
  <strong>Reins: Take control of your AI agents.</strong><br/>
  <code>pip install reins</code>
</p>
