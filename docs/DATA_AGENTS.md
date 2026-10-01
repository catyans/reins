# Data-agent execution optimization

Reins now provides three composable pieces: bounded online batching, frozen
task-specific policy selection, and exact workflow checkpoints. These are local
single-process components. They are not a distributed job platform or a claim of
an independently learned general-purpose agent.

## Online batching

```python
from reins import BatchKey, DeadlineBatcher

# This handler must trace and meter its calls and return results in input order.
# Use MicroBatchPolicy / align_by_identity for provider output validation.
async with DeadlineBatcher(handler, batch_size=4, max_wait_ms=50,
                          concurrency=4) as batcher:
    result = await batcher.submit(
        payload,
        key=BatchKey("tenant-a", "supplier", "fields-v1", "policy-v1", "lite"),
        timeout_seconds=10,
    )
```

Submit concurrently to form batches. A batch cannot cross any BatchKey boundary.
Each Delivery reports queue, service and total time, batch identity and whether
the deadline was met. Waiting jobs expire without a model call. Paid shared calls
are allowed to finish when one caller cancels; the other callers are unaffected.
Provider errors propagate without implicit retries. Closing drains queued work.

The scheduler detaches caller context: a shared call must have its own explicit
trace/accounting boundary. Do not assume that the first submitter's task budget
controls all tenants. Enforce budgets inside the traced batch handler. The queue
is in memory; it is not restart-durable.

## Frozen policies

`learn_bundle` takes paired per-item validation reports and versioned
configurations. It selects the cheapest eligible configuration per input-side
segment; segments with insufficient evidence retain the baseline. Quality must
meet both the minimum acceptance and the observed baseline rate, and costs must
be complete. `PolicyBundle.choose` only inspects input features. Persist the
checksummed bundle before evaluating unseen tasks. The bundle is never retuned
by the test runner. Statistical uncertainty is reported separately: equal
observed rates are not proof of equal population quality.

## Exact checkpoint reuse

`CheckpointStore.run` hashes isolation, step, implementation version, source
inputs and upstream revision hashes. Unchanged steps return their stored JSON
results. Changed source or dependency revisions rerun only affected steps.
Callers must include every result-affecting option in inputs/version and must
re-fetch or otherwise validate source freshness before reusing a source snapshot.
There is no semantic cache and no automatic promise that a URL has not changed.

Failed/interrupted attempts remain unsettled and cannot silently incur another
charge. Use `authorize_retry` with a billing reconciliation reference after
investigating. Preserve existing billing reservations through the regular Reins
ledger. Checkpoint recovery does not settle a provider bill.

```bash
PYTHONPATH=src python examples/incremental_data_agent.py --database /tmp/data-agent.sqlite
```

The first iteration computes three steps, the second reuses them, and the third
updates the release and dependent join while preserving the unchanged profile.
This example is simulated; the separate benchmark uses paid Gemini API calls.

## Reproducible experiments

Run `benchmarks/data_agent_dataset.py` to freeze public GitHub/Crossref records
and constructed invoice/update conditions. The records are reformatted task
inputs, not an end-to-end browser crawl or customer deployment. A source parser
is included as a legitimate zero-model-call competitor. Previous update state is
provided equally to all candidates; its original acquisition is excluded equally.

`benchmarks/data_agent_live.py` records original answers, usage, cost estimates,
queueing, fallback and exact acceptance. The comparison unit is an individual
data result; shared call costs are allocated equally across its inputs. Fresh
output directories prevent overwriting a paid experiment. Missing usage and
timeouts stay incomplete and block economic recommendations. The spend envelope
is a conservative experiment guard, not an account billing cap.

The public Strands methods are run by `benchmarks/strands_gemini.py` in a separate
environment pinned to upstream commit `da3dd085e987a2105382f9a883b145b26b06608a`.
Its original loops, sampling and prompt templates are retained. Gemini replaces
Bedrock; a bounded trace-file toolset replaces unrestricted shell and the default
swarm. All changes are recorded. These are adapted algorithm experiments, not
AgentCore managed-service benchmarks or exact reproductions of AWS's published
model/workload results. Failed and guardrail-rejected optimization attempts are
retained, including their costs.

## Amazon comparison

AgentCore already supports observability, evaluation, system-prompt
recommendations and A/B testing. Those are not exclusive Reins features.
Intelligent Prompt Routing's documented application-history limitation applies
to that router, not to everything that can be built with AWS. Reins focuses on
application-owned outcomes and complete task execution decisions: reuse, batch,
model choice and selective repair. The benchmark must establish whether these
decisions improve a particular workload compared with competent alternatives.

Sources, checked 2026-09-30:
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html
- https://github.com/strands-labs/harness-optimizer
- https://ai.google.dev/gemini-api/docs/pricing

The product website replays recorded experiments only. No public endpoint spends
Gemini credits. Image-generation experiments and their shared review service are
separate workloads, not evidence for LLM-agent performance.

## Read the measured outcomes correctly

The audit's frozen router is a candidate, not a deployed recommendation. The
post-test adoption gate requires at least 300 paired cases, at least 95% observed
acceptance, no observed acceptance drop relative to the frozen comparator,
p95 total latency at most 60 seconds and strictly lower cost per accepted result.
It never changes the frozen policy after seeing held-out answers. Matching a
zero-model-call parser cannot pass the strict cost-improvement gate.

The incremental-refresh candidate reduces model inputs to changed fields. Compare
it both with fixed-model refresh and the deterministic parser, not just an
expensive model. Type errors (for example, a number replacing an amount string)
are rejected by the exact-field outcome evaluator. The runtime's source-presence
check alone does not guarantee type correctness; production integrations need
schema/type validation before they accept an output.

The current audit does not include real unstructured customer webpages, randomized
simultaneous provider control, human labeling costs or infrastructure prices.
Further product validation should use genuinely unstructured sources with
human-reviewed schemas, then a new untouched test split. Existing results must
remain unchanged when validators or routing rules improve.
