# Reduce work before paying for a larger model

Reins now supports microbatch policy execution and quality-constrained
configuration selection. For offline extraction jobs, multiple independent
inputs can share one model request. A caller-defined deterministic transform can
perform arithmetic or another verified business operation before output validation.

```python
from reins.batching import MicroBatchPolicy, align_by_identity

# batch_agent returns a map: local string index -> parsed business answer.
# Normalize list/map response shapes by a unique business ID, never list order.
policy = MicroBatchPolicy(
    batch_agent=extract_batch,
    fallback=extract_one,
    validator=validate_from_source,
    transform=calculate_from_explicit_source,
    batch_size=8,
)
result = await policy(inputs)
```

The callables can be synchronous or asynchronous. They see inputs and outputs,
never reference answers. Missing or invalid items are retried individually with
the fallback. Unexpected local index keys invalidate the mapping. Duplicate
business identifiers are removed by `align_by_identity` so they can be retried.
An invalid fallback produces `None` and an error entry. Provider failures and
cancellation propagate without secretly issuing more paid calls.

Wrap execution in `@trace` so provider attempts, including fallback, enter the
same ledger. `BatchResult` contains outputs, fallback indices and format/validation
errors. Its output length preserves the input correspondence. The optional
transform is applied to primary and fallback outputs; it must derive changes
from trustworthy source facts rather than guessed labels.

## Choosing a configuration

`select_configuration(reports, baseline=..., min_acceptance=0.95, min_cases=30,
max_p95_seconds=...)` compares paired **validation** records. Each report includes:

- `policy`, `records` with unique `id` and Boolean `success` for each input;
- `total_cost`, `cost_complete` (explicitly true only when settled);
- `p95_wait_seconds`, measured batch-service time excluding time spent queued
  behind earlier batches.

It selects the lowest cost per accepted item satisfying both the acceptance floor
and the baseline's observed acceptance, plus any service-time limit. Unsettled
baseline cost, missing/duplicate cases and low sample counts cannot justify a
recommendation. Export/freeze the selection before running a separate audit.
The gate is empirical; it is not a statistical guarantee of unchanged quality.

A batch is one traced root run, while the business unit may be an invoice. **Do
not compare per-batch cost across different batch sizes as per-invoice cost.**
The benchmark explicitly divides total model spend by accepted invoices. The
ordinary SDK comparison view still reports the unit marked by `@trace`.

## Real-call benchmark

`benchmarks/google_batch_live.py` compares single Flash-Lite, a competent verbose
batch-of-four baseline, and compact batches of eight/sixteen. Every policy uses
the same identity alignment and deterministic calculator. Prompts and schemas
are frozen before collecting validation results. A new audit split checks the
selected configuration alongside both baselines.

This is real API execution over constructed invoice documents, not a customer
rollout. Batching trades individual completion time for job throughput. It is
appropriate for independent offline jobs, not automatically for interactive or
dependent agent steps. The calculator example handles one explicit tax-free
subtotal/discount/shipping grammar and abstains outside it; it is not a general
financial document parser.
