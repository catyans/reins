# Practical runtime controls

Reins can now stop repeated paid work, coordinate a customer/team budget across
workflows, protect funds for critical tasks, and preserve a separate allowance for
saving partial results. These controls run before dispatch in the existing local
ledger transaction. They do not replace the website or the previous optimizer.

## Try the complete local flow

```sh
pip install -e '.[dev,all,proxy,sdk-test]'
python examples/practical_controls.py --serve
```

Open `http://127.0.0.1:8796` and use the generated
`output/practical-demo/operator.token` file to connect. This example uses
**simulated tasks and costs; it makes no provider calls**. Choose a fresh
`--output` directory for another run. The dashboard supports pause/resume,
terminal wrap-up, exact-call approval, task history, context composition,
customer/team cost summaries, and loading a regression report.

For real work, run `reins control serve`. It creates separate private operator
and worker token files. Operators provision workflow policies, pools and tools;
workers bind to a provisioned task using `Client(token_file=...worker.token)` and
`bind_context`. Workers cannot create new workflows, change a registered tool,
approve themselves, import invoices, or resume a paused workflow. Existing
operator-token SDK usage remains supported.

This is a **trusted, single-host workspace**, not tenant isolation or a sandbox
for untrusted Python. A shared worker credential can inspect/bind registered tasks
in that workspace. Give it only to trusted worker processes. Tool policies govern
calls routed through these APIs, not arbitrary external calls.

## What each research need maps to

| Research direction | Available now | How to use it |
|---|---|---|
| Runtime budget and runaway control | Atomic customer/team pools, critical reserve, exact-repeat and consecutive-failure limits, depth/tool/deadline limits, wrap-up funds, async calls | Provision limits, call `Workflow.call` / `acall` before paid work, pass `operation_inputs` for repeat detection |
| Cost accuracy and reconciliation | Durable reservations, explicit prices/aliases, native and compatible Gemini usage, immutable CSV invoice revisions | Unknown charges remain held; use `reins control reconcile --csv ...` with real request IDs |
| Total cost and attribution | Workflow/task lineage, customer/team rollups, external review and infrastructure entries | Record human hours/rate and infrastructure allocations through `economics` |
| Unit economics | Recorded revenue, total costs, margin after explicit coverage confirmation; existing history-based forecasts | Record revenue and `coverage_complete`; incomplete data does not produce a margin |
| Context/token overhead | Data Agent and LangChain component estimates, provider totals kept separate, revision comparisons, explicit stage tool/retrieval selection | `prepare_context`, `context_revision_diff`, and LangChain `reins_context` metadata |
| Quality and reliability | Registered tool allowlists, typed required arguments, scalar scope allowlists, output validation, bounded failure circuit | Register tools then invoke `call_tool`; use a business validator |
| Evaluation and regression | Required tool sequence/count, named validations, approvals, cost/latency gates, frozen case coverage, readable report | Export `regression/case`, evaluate a frozen suite, fail CI on gate errors |
| Production stability | Async cancellation holds charges; paused tasks settle in-flight work; existing data Agent resumes saved read-only results | Use `reins projects resume` and existing checkpoints; ambiguous paid work is not retried automatically |
| Security and permissions | Operator/worker separation; write calls require exact, expiring, single-use approval | Operator registers policy and approves arguments; worker consumes approval during admission |
| Multi-agent coordination | Durable parent/child budgets, structured handoff, shared state version check | `write_state`, `handoff`, `bind_context` within one registered workflow |
| State and memory | Source and expiry on versioned state, conflict rejection, explicit corrections | Read latest version before writing a correction; expired or stale handoffs are rejected |

Semantic correctness of arbitrary memory, automatic task ROI decisions, learned
semantic-loop detection, distributed admission, enterprise RBAC, and universal
provider invoice import remain outside this release. A manual cost-coverage
assertion is not independent evidence that every business cost was captured.

## Provision a budget pool and workflow

Operator JSON documents can be submitted with:

```sh
reins control apply pools pool.json
reins control apply workflows workflow.json
```

`pool.json`:

```json
{"pool_id":"research-october","customer_id":"acme","team_id":"research",
 "budget":"20","critical_reserve":"4"}
```

`workflow.json`:

```json
{"workflow_id":"daily-research","customer_id":"acme","team_id":"research",
 "pool_id":"research-october","priority":"normal","task_type":"data-agent",
 "budget":"1","mode":"enforce","wrapup_reserve":"0.1",
 "failure_limit":3,"repeat_limit":3,"repeat_window_seconds":60,
 "max_depth":2,"max_tools":30,"max_iterations":100}
```

Pool identities/allocations are immutable. Create a new pool for the next budget
period. Admission checks both aggregate committed spend and the noncritical
allocation, then every ancestor's task cap, in one transaction. Settled actual
cost replaces a reservation; unknown cost never releases it. `observe` records
budget/limit violations without blocking; use `enforce` for actual limits.

`operation_inputs` must identify the actual operation. Dictionary key order is
normalized. This detects exact repeats within a task, not semantic equivalence.
Consecutive failures are per task and use recorded operation outcomes; a success
resets the consecutive count. Normal limits are disabled unless configured.

`run.wrapup()` permanently closes exploration for that workflow. One explicit
`call(..., phase="wrapup")` can save results using remaining funds. It still obeys
all ancestor and pool budgets and tool approvals. It cannot create branches or
resume normal exploration. Finish partial work with `accepted=False` unless the
business acceptance contract really passed. Stopping future dispatch does not
cancel or refund a provider request already in flight.

## Connect the existing data Agent

After provisioning the workflow above:

```sh
reins projects run --dataset public-projects.json --output output/data-agent \
  --policy grouped-incremental --budget 5 \
  --control-task daily-research \
  --control-token-file ~/.reins/worker.token
```

Supply Gemini credentials through the existing environment variables or private
`--key-file`. The agent retains its local execution/checkpoint history while the
central service admits and settles the same call once. The local experiment cap
remains an additional guard. The execution store and any active trace retain views of the same calls; **do
not sum their cost totals**. Business outcomes remain explicit: the operator or
integrating application calls `finish(accepted=...)` after validating the job.

## Tool approval and shared state

A tool policy includes exact required fields/types, optional per-field
`allowed_values`, `read_only`, and `approval_required`. All write tools require
approval. `call_tool(name, execute, arguments, ...)` passes the same arguments to
the bound Python callable and the admission service. Each callable returns
`(JSON result, actual USD cost)`. Its declared bound must cover any nested work.

An approval binds the task, tool policy digest and canonical argument digest,
expires within an hour (five-minute default), and is consumed atomically with the
reservation. A changed argument, changed policy, another task, expiry, or second
request cannot use that approval. A lost admission acknowledgement leaves it
consumed; do not repeat a potentially dispatched write.

```python
run.write_state("sources", {"urls": source_urls}, expected_version=0,
                source="source-collector", ttl_seconds=3600)
transfer = run.handoff("extract-child", goal="Extract verified fields",
                       inputs={"schema":"v1"}, completed=["collect"],
                       state_name="sources", version=1)
```

The receiving task must already belong to this workflow. Handoffs require a
structured goal/input/completed envelope and current, unexpired state. State is
workflow-scoped; a conflicting update is rejected instead of silently overwriting
another agent's work. A correction is a new version, preserving the old record.

## Regression contract

`call` / `acall` accept `validator` and `validation_name`. A failed validator still
settles the paid cost. Named checks appear in the exported recorded trajectory.
Tool approval evidence comes from consumed ledger approvals, not a UI assertion.

```sh
reins control apply regression/case case-request.json
reins control regression frozen-suite.json --output output/regression
```

The suite contains `expected_case_ids`, `cases`, and `contract`. Example contract:

```json
{"required_tools":["fetch","verify"],"ordered_tools":["fetch","verify"],
 "max_tool_calls":5,"required_validations":["source_supported"],
 "approval_tools":["publish"],"max_cost":"0.02","max_latency_ms":10000}
```

Missing/duplicate cases fail the suite. Missing measured cost/latency fails a
requested gate. A correct final answer does not pass when a required verification
step is missing. The command writes HTML and JSON and returns exit code 1 on a
failed gate. The report viewer does not execute another model call. Imported
case documents are caller-supplied evidence; retain the originating ledger.

## State migration and validation

Ledger schema v1 migrates transactionally to v2. A private SQLite backup at
`<database>.pre-v2.sqlite` includes committed WAL data. A failed migration rolls
back its new tables and releases the process lock. Existing workflows and
reservations are preserved. Reopening v2 is idempotent. A newer schema is rejected.

See [validation and live measurements](PRACTICAL_CONTROLS_VALIDATION.md) for tested
failure paths, all measured baselines, and the distinction between normal traffic
and constructed repeat-loop conditions.
