# Runtime control validation — October 1, 2026

The new control path preserves accepted outputs and matches the strong static
Flash-Lite baseline's API cost in this finite integration experiment. It does not
establish incremental model-cost savings beyond ordinary caching and model choice.

## Real Gemini calls

Twelve fresh public PyPI package records, each requested twice, were frozen
before model execution. Each result had to exactly match the source's package
name, version and Python requirement, including null values. No failing case
was removed and the acceptance rule was not changed after execution.

| Policy | Accepted requests | Actual API calls | Usage-priced total |
|---|---:|---:|---:|
| Flash + exact cache + static budget | 24/24 | 12 | $0.0017136 |
| Flash-Lite + exact cache + static budget | 24/24 | 12 | $0.0003404 |
| Reins shared control + Flash-Lite + exact cache | 24/24 | 12 | $0.0003404 |

The 24 requests contain **12 unique records**, not 24 independent quality samples.
Each arm reused twelve outputs. Reins recorded twelve admissions, twelve
settlements and twelve validated reuses with no unresolved charges.

The cheaper model cost **80.14% less than Flash** for these records. The
runtime-controlled arm was **equal to the static Flash-Lite baseline**, not
80.14% better than that baseline. The structured records also admit a deterministic
parser with zero model API cost; model use here tests integration, not necessity.

Costs are complete returned token usage multiplied by
[pinned standard prices](https://ai.google.dev/gemini-api/docs/pricing), checked
October 1, 2026. They are not provider invoice debits. No model-based evaluator
was used. Tools performed public HTTP reads without a paid API fee. Local CPU,
network, storage and engineering costs are not included in API-cost totals.
Execution used zero SDK retries, a 256-token output cap and thinking budget zero.

Single sequential arm wall times were 10.00, 6.32 and 7.88 seconds respectively.
They are descriptive observations, not a controlled latency superiority claim.

## Runtime safety and compatibility

The automated tests exercise:

- Four independent processes sending 100 requests against one $1 budget.
  Exactly 33 reservations of $0.03 were admitted: $0.99 committed.
- A second process cannot own the same database. Restart preserves reservations,
  and replaying an admitted request ID does not authorize another dispatch.
- Parent and child caps constrain the same operation without double counting.
- Approved-model fallback changes the actual SDK dispatch and settles its usage.
- Uncertain charges stay held; billing revisions are immutable and unmatched
  invoice lines remain visible.
- Service outages fail closed in enforce mode and warn in observe mode.
- Forecasts require 30 matching measured histories and exclude snapshots with
  unresolved charges. Histories do not mix customers.
- Explicit input-token attribution keeps unattributed framing visible.
- The local dashboard passes desktop and 390px mobile overflow checks with
  no browser JavaScript errors.

The full local suite passed **231 tests** at this checkpoint. CI separately
checks Python 3.10–3.13, lint and formatting. This is single-host coordination;
it is not a distributed stress test or a proof against arbitrary uninstrumented
provider calls.

## Reproduce

Run the control tests:

```sh
pytest tests/test_control.py -q
```

Run a new, finite paid smoke experiment using a fresh output directory:

```sh
PYTHONPATH=src python benchmarks/runtime_control_live.py \
  --key-file /absolute/path/to/your/local.env \
  --output output/runtime-control-new
```

The script records the frozen sources, protocol, raw model outputs, usage,
per-arm summary, and durable control ledger. It refuses to overwrite an existing
experiment directory. Fresh PyPI records may differ; use the frozen original
sources when reproducing the exact dataset.

Original local evidence: `output/runtime-control-20261001/`.
The dataset digest and aggregate results are retained in
[`assets/runtime-control-validation.json`](assets/runtime-control-validation.json).
Raw local ledgers and credentials are not published.
