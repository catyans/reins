# v0.2 SDK pilot guide

Configure once, before starting tasks. Do not reconfigure while requests are in flight.

```python
from reins import configure
configure(config_path="reins.yaml", token_counter=trusted_input_counter)
```

`trusted_input_counter(provider, model, request)` must return a nonnegative integer
that bounds all billable input tokens for that exact request/model. Use an official
provider count endpoint where available, or a validated model-specific tokenizer
including message/tool formatting. Do not use the demo's constant counter in production.
The callback is synchronous; slow network token counters add admission latency.

Example **synthetic** configuration (replace model IDs and prices with verified ones):

```yaml
mode: observe
policy_version: extract-v1
storage_path: ./pilot.duckdb
prices:
  openai:
    demo-strong: ["10", "10"]
    demo-small: ["1", "1"]
task_models:
  field_extraction:
    - openai/demo-strong
    - openai/demo-small
budgets:
  daily: "$10"
  monthly: "$100"
  agents:
    supplier_research:
      per_run: "$0.50"
      daily: "$5"
      on_exceed: degrade
```

Global period limits are shared by all agents. An agent's period limits count its
own calls; nested calls additionally consume every enclosing task's per-run cap.
Decorator and configured per-run caps are both honored (the lower applies).
Decorator strategy overrides YAML when supplied; mode defaults to configuration.
An enforced parent cannot be bypassed by setting a child to observe.

## Supported execution

Use `@trace(task_type="field_extraction", policy_version="...")` around a complete
task. SDK clients should use `max_retries=0` in enforce mode. Each SDK `.create`
request needs a finite output bound. Streaming must be consumed using the returned
wrapper, a context manager, or explicit `close()` / `aclose()` on interruption.
Exceptions with unknown billing retain the reservation until reconciliation.

Model choices must be evaluated for the exact request schema, tool calls, and
output requirements before adding them to a task allowlist. Reins does not silently
remove unsupported parameters or switch providers. Price overrides are immutable
for a configured runtime; historical ledger amounts remain as recorded.

## Outcomes, retries and external fees

```python
from reins import record_outcome, record_external_cost, record_retry, reconcile_cost
record_outcome(success=True, score=1.0, reason="all required fields match")
record_external_cost("0.003", label="paid source lookup")
record_retry()  # only when your application actually retries a task/step
# Later, after inspecting provider usage/billing:
reconcile_cost("request-span-id", "0.012")
```

An explicit `run_id=` lets outcomes be attached later. Scores are finite [0,1].
Root tasks are the reporting unit; nested model and external costs roll up once.
External fees are reported after they occur and are not governed by the LLM budget.
The billing metric therefore covers captured LLM calls plus reported external fees,
not unreported infrastructure or human labor.

## Pending fees and integrity

Budget transactions are durable and use decimal amounts. Unknown price or missing
usage remains pending. A pending known-price request holds its entire reservation;
an unpriced request blocks subsequent enforce-mode admission until reconciled.
A provider charge above its reservation is retained accurately, raises
`budget.bound_exceeded`, and blocks further enforced calls until the operator checks
and acknowledges it via reconciliation with that amount. No hard-cap claim applies
if the caller's token bound or pinned prices are wrong.

Only one writer per local database is supported. DuckDB owns cross-process file
locking; same-process duplicate writers are rejected. This is not a distributed
quota service. Keep customer pilots in separate databases. Trace retention does
not delete budget records needed for accounting.

## Observation-only integrations

Adapters are trace callbacks, not spending authorities. The local HTTP proxy is
experimental observation-only and accepts `/day` or `/month` limits for reports.
Enforce configuration is rejected by the proxy. Use the SDK path for a paid pilot.
