# Outcome-based optimization

Reins selects an execution policy for a repeatable business task under explicit
quality and latency constraints. Start with data collection and structured
extraction. The current optimizer compares caller-supplied policies; it does not
train a router, compress models, search hardware configurations, or deploy changes.

## A complete local example

```bash
PYTHONPATH=src python examples/outcome_optimizer.py \
  --database /tmp/reins-optimizer.duckdb --dashboard --port 8766
```

Open http://127.0.0.1:8766 and choose **策略优化**. The example compares a strong
candidate, a small candidate, and a small-first policy with source validation and
one strong fallback. All answers and fees are synthetic. There are 40 validation
cases and 20 separate test cases. Test cases follow the same synthetic generator;
this checks software behavior, not real-world generalization. Synthetic timing
measures local Python execution, not model latency.

The cheap candidate fails the quality gate. The cascade includes the cost of the
first attempt, online validation, fallback and revalidation. The report is an
empirical comparison, not a savings claim for real providers.

## Connect real candidates

```python
from reins import configure
from reins.optimization import Constraints, ValidatedCascade, evaluate_experiment

configure(storage_path="experiment.duckdb", mode="observe")

# Supply your actual functions and a source/evidence validator.
# Provider calls use supported instrumented SDKs. Other tools, evaluators and
# allocated GPU costs must call record_external_cost inside the active task.
cascade = ValidatedCascade(small_agent, strong_agent, validate_source,
                           name="source-cascade-v1")

report = await evaluate_experiment(
    validation_cases,
    {"strong-v1": strong_agent,
     "small-v1": small_agent,
     "source-cascade-v1": cascade},
    baseline="strong-v1",
    constraints=Constraints(min_success_rate=0.95, max_quality_drop=0.0,
                            min_cases=30, max_p95_ms=5000),
)
```

Each case is `{ "id": "unique-id", "input": ..., "expected": { ... } }`.
The default evaluator requires every expected field to exist and match exactly;
extra fields are not checked. For business-specific evaluation pass a sync/async
`evaluator(answer, expected) -> score` in [0,1] and an explicit
`evaluator_version`. Only a score of 1 counts as accepted. A failed run counts as
failure and its known expenses remain in the numerator.

The online `validate_source(input, answer)` must return a Boolean. It receives
no expected labels. A failed validation invokes fallback once. If fallback also
fails validation, `OutcomeRejected` propagates. Exceptions, cancellations and
budget rejections propagate without starting another potentially costly call.
Use an outer `@trace` for production execution, then record the independently
verified business outcome. Online validation is a routing signal, not ground truth.

Every candidate receives a fresh copy of input. The optimizer never passes case
IDs or expected answers to a candidate. Use distinct, versioned names for model,
prompt, validator and serving configuration combinations; Reins cannot verify that
a caller keeps the implementation behind a name unchanged.

## How selection works

1. Persist an experiment manifest before invoking any candidate.
2. Run all candidates on the same cases, evaluator and execution mode. Each gets
   one observation per case. Candidate order is sequential; randomize external
   benchmark scheduling when temporal provider effects matter.
3. Check case pairing, cohort identity, complete outcomes and baseline accounting.
4. Require `success_rate >= max(min_success_rate, baseline_rate - max_quality_drop)`.
5. Apply minimum unique cases and optional p95 duration limits.
6. Among eligible candidates choose the lowest **total recorded cost / accepted
   tasks**. Prefer the baseline on equal cost. A more expensive eligible candidate
   can be recommended if the baseline misses another constraint; the savings field
   will be negative.

Costs include failed attempts and nested runs exactly once, plus reported external
fees. Unknown/pending costs prevent selection of that candidate. An incomplete or
unpriced baseline blocks recommendations and relative savings. Unrecorded tools,
GPU idle time, training/calibration, human review and other overhead are **not
magically included**: allocate and report them consistently before comparing.
External cost reporting does not reserve or enforce a budget for that external tool.

The quality gate uses observed rates, not a statistical non-inferiority test.
Wilson intervals describe individual rates only. The default 30-case minimum is
an engineering guardrail, not proof of a 95% production success rate. Use a larger,
representative set with difficult and failed examples for actual deployment decisions.

## Freeze, test, deploy

Keep the chosen callable/configuration unchanged. Evaluate it and the baseline
on disjoint inputs using `split="test"`. Test reports intentionally never select a
new winner and return no selection savings field. Do not repeatedly tune on that
set. The SDK separates experiments but cannot certify that your datasets are
semantically disjoint. Export reports, review the quality/cost tradeoff, then wire
the selected callable into your application explicitly. There is no automatic
production promotion or live traffic mutation in this release.

```bash
# Stop the writer process before opening the same DuckDB through the CLI.
reins experiments --database experiment.duckdb
reins optimize --database experiment.duckdb --experiment EXPERIMENT_ID \
  --output recommendation.json
# Optional policy constraints override the original experiment settings.
reins optimize --database experiment.duckdb --experiment EXPERIMENT_ID \
  --min-success-rate 0.98 --max-quality-drop 0 --min-cases 100
```

The running SDK dashboard exposes read-only `/api/experiments` and
`/api/optimization?experiment=ID` on loopback. It uses the same recommendation
engine as the CLI. Experiments are isolated by ID in the cost comparison view.

## Product sequence

- **Now:** paired policy experiments, task outcomes, complete recorded-cost
  accounting, empirical quality/latency gates, explicit validated fallback,
  budget enforcement and execution visibility.
- **Next customer pilot:** one real data-extraction workflow, labeled acceptance
  examples, fixed strong/small and simple escalation baselines, independent test
  results, manual rollout and rollback procedures.
- **After evidence:** specialized routing and compressed/self-hosted deployment
  candidates. Athena/QET and inference-engine integrations should enter the same
  task-quality test. Generation-step optimization belongs to image workloads;
  it is not interchangeable with an LLM's reasoning or an Agent's tool steps.

The first product milestone is lower cost for an accepted customer result, not
more monitoring charts or a promise to optimize every AI workload.
