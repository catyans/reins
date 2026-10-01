# Field-extraction pilot

## Deliverable

Give an Agent product team a comparison of fixed-strong, fixed-small, and an
explicitly approved Reins budget policy on the **same** task fixtures. The customer
owns the pass/fail rubric. Start with supplier/business fields, not subjective answers.

## Data contract

A fixture is JSON-compatible:

```json
{"id":"case-001","input":{"page":"de-identified page contents"},"expected":{"country":"US","name":"Acme"}}
```

The harness checks exact equality for every expected field; extra output fields do
not affect the score. Normalize dates/names before constructing fixtures. Reject
empty expected dictionaries and duplicate IDs. Do not tune against the held-out set.

```python
from reins.evaluation import evaluate_dataset
await evaluate_dataset(cases, agent_function, policy_version="fixed-strong",
                       task_type="field_extraction", budget="$0.50", repetitions=3)
```

`agent_function(input)` may be sync or async and must return a field dictionary.
Its actual model calls go through the SDK. Exceptions become failed tasks; billing
uncertainty remains pending. Each case/repetition is a separate traced run; dataset
hash, case id and policy version are persisted. Use a fresh database for separate
experiments. The harness does not read arbitrary websites or send external messages.

## Protocol

1. Agree on required fields and unacceptable error types with the customer.
2. Collect at least 100 held-out examples covering common, ambiguous, missing-field
   and long-page inputs. Record source permission and remove sensitive content.
3. Run the baseline and candidates on identical fixtures, with three repetitions.
   Include retries and `record_external_cost` for paid sources and evaluation.
4. Resolve pending model charges before comparing cost per accepted result.
5. Inspect quality by case type, not just the aggregate. The report's Wilson interval
   is a rough binomial summary; repeated cases are correlated. It is not a formal
   noninferiority certificate. Preserve outputs with the customer for review.
6. Initial internal gate: at least 20% lower cost per successful task with no more
   than 2 percentage points loss in task success. These are targets, not achieved
   results. If uncertainty or critical errors remain, keep observation mode.
7. Pilot deployment: observation first, then 5% of eligible tasks using an external
   feature flag; retain fixed-model rollback. Review latency, failures, pending fees,
   and customer corrections daily. Do not automatically expand traffic.

The offline demo is intentionally synthetic: cheaper calls lose accuracy on an
ambiguous case. It verifies the product loop and provides no commercial performance
claim. Real quality-aware routing must be learned from the pilot workload.
