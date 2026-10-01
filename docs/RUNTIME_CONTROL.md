# Agent Runtime Economic Control

Reins now has an optional **single-host control service** for agents running in
separate processes. Each paid operation reserves part of a shared workflow budget
before execution. Its actual usage releases unused reservation; an ambiguous
timeout keeps the reservation held.

This extends the existing trace, outcome evaluation, and policy optimizer. It
does not replace the current website or measured cases.

## Run locally

Install this checkout, then start one service:

```sh
pip install -e ".[all]"
reins control serve
```

Open **http://127.0.0.1:8795**. Paste the token from
`~/.reins/control.token` into the local dashboard. The token stays in tab memory;
it is not put in URLs, local storage, or exported task contexts. The page is
read-only. CLI operator commands can pause/resume work and reconcile charges.

The service binds to loopback, requires a credential, checks Host/Origin, and owns
one SQLite WAL database. Worker trace databases remain separate DuckDB files.
This is a trusted local workspace, not a remotely exposed multi-tenant service.

## Add a workflow and explicit paid tools

```python
from reins.control import Client, workflow

control = Client(token_file="/absolute/path/to/.reins/control.token")

with workflow(
    client=control,
    workflow_id="research-2026-10-01",
    customer_id="customer-17",
    task_type="supplier-research",
    policy_version="research-v1",
    budget="1.00",
    mode="enforce",
    max_branches=8,
    max_iterations=40,
    max_retries=2,
    approved_models=["google/gemini-2.5-flash", "google/gemini-2.5-flash-lite"],
) as run:
    with run.task("source-collection", budget="0.40") as task:
        result = task.call(
            fetch_paid_source,  # Returns (JSON-serializable result, USD cost string).
            model="source-provider/search",
            category="tool",
            max_cost="0.02",    # Must bound the entire operation, including internal retries.
            read_only=True,
            reuse_inputs={"url": source_url, "revision": frozen_revision},
            validator=validate_source,
        )
    run.progress("collect", completed=1, total=10)
    run.finish(accepted=business_acceptance(result))
```

The code deliberately requires business acceptance. Returning from a Python
function is not evidence that the task succeeded. If a validator rejects an
output, the paid operation is still charged and that output is not reused.
Include source revision, extraction contract, and validator version in
`reuse_inputs` whenever those can change. Cached values are scoped to a workflow.

Use `category="model"` for explicit model calls and `"evaluation"` for paid
evaluators. Paid tools/evaluation calls count toward the same budget. The caller
must provide honest bounds and complete usage; arbitrary external HTTP calls are
not automatically intercepted.

## Use existing SDK instrumentation

```python
import reins

reins.configure(
    storage_path="/tmp/worker-1.duckdb",  # Unique per worker.
    control_token_file="/absolute/path/to/.reins/control.token",
    prices={"openai": {
        "your-model": ["INPUT_USD_PER_MILLION", "OUTPUT_USD_PER_MILLION"],
    }},
    token_counter=verified_input_bound,
)
with reins.workflow(customer_id="customer-17", task_type="research",
                    budget="1.00", mode="enforce") as run:
    # Existing instrumented OpenAI / Anthropic calls now reserve centrally.
    # Set SDK max_retries=0 and explicit max_tokens/max_completion_tokens.
    response = sdk.chat.completions.create(...)
    run.finish(accepted=validate(response))
```

The native Google SDK is not patched; use the explicit paid-call wrapper or the
existing OpenAI-compatible Gemini route. Unknown/missing/special billing usage
stays reserved until explicit reconciliation.

An exhausted shared budget can switch an SDK call to the next **explicitly
approved same-provider model**, with a new verified input bound and pinned price.
If no approved model fits, execution pauses before the provider call. The model
approval list is supplied by the task owner, not guessed by Reins.

Explicit `task.call` suppresses nested central SDK admission to prevent counting
the same paid operation twice. Its declared bound must cover all nested charges.

## Multi-process handoff

Inside a child task, call `task.export_context()` and pass that small dictionary
through the work queue. The worker uses:

```python
from reins.control import bind_context
with bind_context(context_from_queue, client=control) as task:
    # Paid calls belong to the original customer, workflow, and parent task.
    ...
```

The service verifies registered ownership. Workers load their credential locally.
Do not share a DuckDB file across workers. A child cap and every ancestor cap
constrain the same reservation; costs are counted once in workflow totals.

## Failure and control semantics

| Situation | Behavior |
|---|---|
| Budget / branch / paid-iteration / explicit retry limit | Enforce blocks admission; observe records what would have happened |
| Duplicate request ID | Held; never treated as permission to dispatch again |
| Identical read-only call with validated persisted output | Reuse without another paid call |
| Identical read-only call still unresolved | Held; no speculative duplicate |
| Process crash / missing provider usage | Reservation stays committed across service restart |
| Service unavailable | Enforce blocks new paid calls; observe warns and continues without central observation |
| Confirmed charge exceeds declared bound | Charge recorded, workflow paused, overrun retained |
| Manual pause | Stops future admission, does not cancel already running provider work |

Automatic limits are deterministic. There is no LLM deciding whether to kill a
task. This version does not automatically retry a paid operation, invent a zero
charge after a timeout, or claim it can prevent a provider from billing beyond an
incorrect caller-supplied bound.

## Costs and reconciliation

Amounts use integer nano-USD. Usage-priced costs, unresolved reservations, and
invoice-reconciled charges are separate. A cost per accepted result is shown only
when acceptance is explicit and all operation costs are known.

```sh
reins control status WORKFLOW_ID
reins control pause WORKFLOW_ID --reason "Operator review"
reins control resume WORKFLOW_ID --reason "Review completed"
reins control reconcile --csv invoice.csv
```

CSV columns:

```csv
provider,invoice_id,line_id,request_id,amount,currency,revision,reference
google,invoice-001,line-01,REQUEST_UUID,0.0123,USD,1,invoice-001.csv:2
```

The provider must match the recorded provider/model prefix. Unknown request IDs
remain unmatched. Corrections use the same line identity with the next revision;
old revisions remain auditable. Import is per-row, idempotent, and restartable.
It does not scrape cloud billing accounts or claim a universal provider invoice
schema. A provider bill without request IDs needs an explicit external mapping;
Reins does not invent per-request attribution.

Use `reins.control.usage.token_attribution` for measured system/history/retrieval/
tool-schema/user component counts. The remainder is explicitly unattributed.
Missing component data is not labelled “waste”; SDK totals alone cannot reveal
how much input was useful.

## Forecast and task economics

Report explicit `stage, completed, total` progress. After **30 fully measured
completed workflows** from the same customer, task type, policy, stage and
progress position, Reins returns empirical remaining-cost P50/P90. It excludes
history whose progress snapshot included unresolved charges.

These are historical percentiles, not calibrated guarantees. No forecast is
invented for an unseen workload. `max_cost_per_accepted_result` can request a
review when current committed cost plus P90 exceeds the target; this remains
advisory and never automatically stops work. Business value and probability of
success are not inferred, so this is not a general ROI estimator.

## Validation and current boundary

[Measured validation report](RUNTIME_CONTROL_VALIDATION.md): 24/24 accepted requests
per arm, with API cost equal to the strong static Flash-Lite baseline.

- Four processes, 100 competing requests, exact shared budget.
- Cross-process parent/child attribution, restart-held reservations and no duplicate dispatch.
- Exact USD accounting, invoice revisions, unmatched charges, and overruns.
- Real SDK dispatch gating and approved model fallback.
- Auth/origin checks, explicit cached-output validation, and historical forecast gating.
- Existing test suite and website evidence retained.

`benchmarks/runtime_control_live.py` freezes fresh public records and a strict
acceptance rule before Gemini calls. It compares ordinary exact caching plus
static budgets on Flash and Flash-Lite against runtime-controlled Flash-Lite.
This is an integration smoke study: structured source records also have a
zero-API-cost deterministic parser baseline. It cannot establish production
savings or superiority to a well-implemented static policy.

Not included: distributed consensus across hosts; remotely exposed control;
provider billing connectors; automatic business-value prediction; semantic
loop detection; general tool-permission policy; memory correction; arbitrary
multi-agent handoff repair. Existing agents keep their original behavior until
they opt into the control service.
