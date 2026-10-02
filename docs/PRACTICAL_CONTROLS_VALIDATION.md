# Practical controls: validation and live evidence

The new controls preserve the existing 231-test baseline. The expanded suite has
**258 passing tests**, including four-process budget contention, migration
rollback, cancellation, exact approval consumption, handoff conflicts, recorded
trajectory gates, and data-Agent recovery. Browser checks exercised actual local
API actions at desktop (1440 px) and mobile (390 px) sizes; no JavaScript errors
or page-level horizontal overflow remained.

## Real Gemini calls

**454 provider calls total**, below the 500-call cap. A 100-case public PyPI source
snapshot and exact three-field acceptance contract were frozen before execution.
No customer data, adaptive quality threshold, model retry or discarded case was
used. These structured records can also be parsed without an LLM; this tests
integration and controls, not the economic necessity of model extraction.

Initial run: controlled Lite completed **100/100** correctly. The Flash and static
Lite wrappers each reported 0/100 because an existing external-cost recorder
required a tracing context after the provider had already returned and the result
had been persisted. This was an integration failure, not poor model quality.
The fix checks for an active trace before adding that optional trace-cost record.
All **200 baseline responses recovered successfully without another provider
call**. Original failed outcomes, paid costs and raw responses were retained.
The first constructed-fault run also encountered this issue and is excluded from
claims of successful task savings; its original evidence remains in the artifact.

We then froze a follow-up using the **first 30 cases from the original snapshot**,
ran all three arms end to end, and repeated the predefined fault scenarios.
This is a separate follow-up, not a retrospective replacement of the initial run.

| Follow-up policy | Accepted | Total API estimate | Cost / accepted | Median latency | P95 latency |
|---|---:|---:|---:|---:|---:|
| existing-flash | 30/30 | $0.0042813 | $0.000142710 | 1007 ms | 1547 ms |
| static-lite | 30/30 | $0.0009047 | $0.000030157 | 907 ms | 1761 ms |
| controlled-lite | 30/30 | $0.0009047 | $0.000030157 | 1037 ms | 1437 ms |

Normal-task incremental savings over static Lite: **0%**. The lower cost relative
to Flash is ordinary model selection. Latency is observational on a shared
provider account with four concurrent requests; these data do not establish a
speed advantage. There were zero unresolved charges, partial outputs or manual
interventions in the successful follow-up. The earlier integration fix and cached
recovery required developer intervention and are recorded separately.

## Constructed repeated-call scenario

Four fixed tasks each attempted the same paid operation five times. No cache was
used in either arm. Static Lite made **20** calls; the configured exact-repeat
limit admitted **12** and stopped **8**. Both retained at least one accepted result
for all **4/4 tasks**. Total usage-priced cost changed from **$0.0006560**
to **$0.0003936**, a **40%** reduction. No charges remained
unresolved in this follow-up.

This measures the configured limiter on an injected repeat-loop condition. It
is not a measured production fault incidence, a semantic-loop detector, or a
proprietary advantage over an equivalent well-implemented static limiter.

## Failure-path checks

- Four worker processes: 100 competing reservations under a $1 workflow admit
  exactly 33 × $0.03. A separate shared pool with $0.20 critical reserve admits
  exactly 26 normal calls × $0.03 across four workflows. Threaded checks also
  verify critical traffic can use protected capacity without exceeding the pool.
- Requests survive process restart. Lost admission replies never dispatch again;
  lost settlement replies preserve committed charges. Cancellation holds the
  reservation, and pausing does not lose an in-flight operation's settlement.
- Exact-repeat windows, changed inputs, success resetting consecutive failures,
  depth/tool/deadline boundaries, one-shot wrap-up, and the pause/resume bypass
  around terminal wrap-up are covered.
- Changed tool arguments, a different task, expired approval, changed policy,
  repeated approval consumption and worker attempts to approve/change policies
  are rejected. Shared state rejects stale versions, expired content and cross-
  workflow handoff.
- Missing usage/model prices never turn into zero costs. Native Gemini thoughts
  and cached input are counted once. Missing cost coverage or pending charges
  leave margins unavailable.
- Correct final output with a missing verification step fails regression.
  Missing/duplicate frozen cases, wrong tool order, missing approval, excessive
  tool count, missing cost and excessive latency fail the corresponding gates.
- v1 data migrates with a private backup; repeated v2 startup is stable; a
  deliberately interrupted migration leaves the original data usable and can
  recover after the conflict is corrected.
- Existing data-Agent cached calls survive restart without a second dispatch;
  a centrally denied call never reaches the provider or leaves a fake local bill.

## Reproduce and inspect

```sh
PYTHONPATH=src pytest -q
ruff check src/ tests/
ruff format --check src/ tests/ examples/
PYTHONPATH=src python benchmarks/practical_controls_live.py \
  --output output/practical-live-new --key-file /private/gemini.env
```

The benchmark freezes sources and protocol before calling the API. Each arm keeps
its SQLite execution state and raw call evidence locally. Public aggregate and
per-case measurements: [JSON evidence](assets/practical-controls-evidence.json).
Readable report: [HTML](assets/practical-controls-report.html).

Costs use provider token usage and pinned standard text rates checked against
[Google's pricing documentation](https://ai.google.dev/gemini-api/docs/pricing)
on 2026-10-02. They exclude infrastructure and engineering costs and are not
invoice debits. Free-tier treatment, cache storage and hosted tools are not part
of these text-only tests. Test results establish these bounded local behaviors;
they are not a distributed-system or enterprise-security certification.
