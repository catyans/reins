# Local validation — 2026-09-29

- Python 3.12: 148 tests passed (including 13 dashboard integration tests).
- Ruff lint, formatting and `git diff --check` passed.
- Provider contract tests use Anthropic 1.9.0 and OpenAI 3.20.0 with local HTTP mock transports. Sync, async and sync streaming paths exercised without network requests or API charges.
- Wheel built successfully. Clean virtual-environment installation, CLI help and synthetic extraction demo passed.
- At that stage, customer interviews, live-model evaluation, paid pilots and multi-version CI had not been run locally. See the subsequent real-API results below.
- Chinese presentation: `output/pdf/reins-pilot-slides.pdf`; editable LaTeX source alongside it. 18 pages rendered and visually checked.

## Dashboard validation

- Browser checks in Chrome: running-task list, parallel collection tasks, business outcomes,
  event timeline, and the three-policy cost/quality comparison.
- Tests cover missing/stale runtime state, retained reservations after restart,
  partial streaming, budget denial, cancellation, nested steps, costs counted once,
  schema-v2 migration, pagination/filtering, private metadata exclusion and port cleanup.
- Installed the built wheel in a separate virtual environment. HTML, JavaScript, CSS,
  run API and comparison API all served successfully; lifecycle recording also passed.
- Ruff checks, Python formatting, JavaScript syntax check and diff whitespace check passed.
- Local dashboard remains a single-process pilot; distributed monitoring and controls
  are outside this release. No production load or live-model savings benchmark is claimed.

## Outcome optimizer — 2026-09-29

- Full suite: **163 passed** (includes 15 optimizer checks); Ruff lint and format pass.
- Paired candidate selection, unknown baseline charges, insufficient samples,
  latency rejection, missing outcomes, duplicate/mismatched cases, input isolation,
  custom evaluator costs, test-split non-selection and CLI report export covered.
- Validated cascade checks include rejection after invalid fallback, strict Boolean
  validators, exception propagation and accounting for all validation/fallback costs.
- Read-only experiment and recommendation HTTP endpoints tested against the same
  engine used by the CLI. Both local website and live SDK API returned HTTP 200.
- Node syntax checks passed for the website and SDK dashboard scripts.
- Synthetic example: strong 100% at $0.030 per accepted task; small 75% at
  $0.005333; validated cascade 100% at $0.012750. This is a constructed test of
  accounting/selection, **not a real-provider benchmark or customer savings claim**.
- Desktop browser visual QA was unavailable: the computer-use tool returned
  `cgWindowNotFound` for both Chrome and Safari. Layout is not visually certified.

## Real API and batching validation — 2026-09-29

- Full suite: **172 passed**. Google OpenAI-compatible instrumentation and eight batching checks added.
- The initial model-only validation winner achieved 58/60 on its test set. That result and two incomplete batching rounds are retained.
- Final configuration selected on 40 validation documents; audited on 100 new IDs/amounts sharing the constructed document patterns.
- Compact 16: 100/100 accepted, 50.0% lower estimated cost and 7.53× sequential-job throughput versus single Lite; 11.0% and 2.03× versus a post-hoc compact-four baseline (also 100/100).
- p95 batch service time: 5.79s selected, 2.48s single, 2.36s compact four. No real-time latency improvement is claimed.
- Main final round plus stronger baseline: 221 API calls, zero provider errors, $0.0265535 estimated token cost, matching the local ledger.
- Across earlier retained rounds, two requests have unsettled cost. They are not treated as free.
- Public website separates measured results from its synthetic interactive demonstration. Browser visual QA remains unavailable; chart render checked separately.
- Results are one provider and a constructed workload, not production quality or customer ROI. See `output/benchmarks/google-batch-v3-20260929/REPORT.md`.
