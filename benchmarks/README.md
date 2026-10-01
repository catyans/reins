# Reins evaluation protocols

## Real Google API comparison

```bash
PYTHONPATH=src python benchmarks/google_live.py \
  --output output/benchmarks/my-run --validation 40 --test 60 --limit 2
```

Supply `DTA_GOOGLE_GENAI__API_KEY` through the environment or the hidden terminal
prompt. Do not put it in arguments, files, screenshots, or reports. The separate
Google Data API key is not needed: this experiment tests extraction from provided
text, not paid search or external data acquisition.

The protocol and fixture set are saved before generation calls. All examples are
constructed invoices with deterministic ground truth, new IDs and values across
splits, and shared task patterns. This is a real-model experiment on a constructed
workload, not a customer deployment or independent public benchmark.

Four fixed policies are evaluated:

1. Gemini 2.5 Flash for every task (baseline).
2. Gemini 2.5 Flash-Lite for every task.
3. A simple source-text rule choosing Flash for higher-risk document patterns.
4. Flash-Lite first, with source/schema checks and escalation to Flash.

The fourth policy accepts the fallback for offline evaluation; its primary gate
is not a guarantee that either answer is correct. The final exact-field evaluator
checks both against the held-out labels. All policies are frozen before calls.
The validation winner is frozen before interpreting test results; test reports
never choose a replacement winner. All four predefined baselines are retained in
the test report to expose when a simple cheap model is already sufficient.

Costs use provider-reported token usage and standard paid API prices as of
2026-09-29. They are list-price estimates, not observed billing debits. Free quota,
credits, discounts, and automatic caching may change actual charges. Cached or
missing usage is treated as incomplete by Reins. No search, retrieval, training,
GPU rental or paid evaluator is used. Local source validation has no API fee; its
CPU time is included in task latency, with machine cost not monetized.

Calls use temperature 0, a 512-token output limit, thinking disabled, no hidden
SDK retries, and short text-only inputs. An accumulated conservative request-cost
envelope stops additional calls near the configured spending limit; it is not
an account-level billing cap. Three provider/transport errors stop further paid
requests rather than switching credentials to evade quota restrictions.

Each policy is run sequentially. Network/load/order effects can influence latency;
these measurements are not causal latency estimates or concurrency throughput.
The default 100 documents are useful for an initial comparison, but insufficient
to establish small production-quality differences or robust behavior across tasks.

Artifacts: protocol, cases, raw answer/usage JSONL, DuckDB runs and separate
validation/test JSON reports. The runner may be formatted after launch; the
recorded source checksum identifies the original run-time file. It must not be
silently replaced when reporting results.

## Runtime overhead

```bash
PYTHONPATH=src python benchmarks/runtime_overhead.py
```

Compare identical SDK requests with a mocked HTTP transport, first without Reins,
then with a trace, local ledger and business outcome. 20 warmups and 200 measured
requests per condition. No paid requests. This isolates local overhead; it is
neither an API speed benchmark nor a scalable server-throughput benchmark.

## Batch optimization rounds

- `google-20260929`: original model/routing comparison, 450 real calls. The
  validation winner (fixed Lite) lost two arithmetic cases in the 60-case test;
  the result is retained, not replaced by a better-looking run.
- `google-batch-20260929`: initial batch validation exposed unnecessary fallback
  on list-shaped output. Stopped during validation; one unsettled request retained.
- `google-batch-v2-20260929`: identity alignment fixed for all baselines. Compact
  batching missed two arithmetic cases in validation. Audit stopped on a provider
  timeout; it is not a complete audit result.
- `google-batch-v3-20260929`: the same deterministic calculator is added to every
  policy; compact batch size is selected before a new 100-document audit
  (`test-200..299`). Inputs are new, but share document patterns with development.
- A compact four-item baseline is additionally tested on those audit cases after
  selection. This is a stronger post-hoc baseline check, not a new selection or
  an additional independent held-out set.

All rounds and incomplete attempts remain in the output folder. Known token costs
and unknown/unsettled costs are reported separately. Never silently count a timed
out request as free. The separate model/routing and batch experiments have different
policy definitions; do not combine their quality or latency into one pooled score.
