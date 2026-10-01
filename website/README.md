# Reins local website

### Public project update pilot

`projects.html` is the new read-only page for real project-update executions. It polls
`project-status.json` every two seconds and reads the completed comparison from
`project-results.json`. The main navigation links to it. The page cannot start a
model call; multiple visitors only read static files.

```bash
python -m http.server 8788 --bind 127.0.0.1 --directory website
```

Open `http://127.0.0.1:8788/projects.html`. Execution is a finite historical-release
replay using real Gemini calls. The website distinguishes active heartbeats, saved
state and completed work. Source support and separate model-assisted semantic
evaluation are reported together; they are not human validation.

The readable export is `reins-project-update-report.pdf`. Full local operation and
evaluation instructions are in `docs/PROJECT_AGENT.md`. No remote deployment or
recurring job is part of this pilot.

Investor-oriented product page with a simulated data-collection case, quota
controls, event diagnostics and an interactive quality/cost comparison.

```bash
python3 -m http.server 8780 --bind 127.0.0.1 --directory website
```

Open http://127.0.0.1:8780. All assets are local. No API keys, analytics or paid
model requests are used. The simulation controls affect only the page.

To regenerate the strategy report and open the real SDK dashboard:

```bash
PYTHONPATH=src python examples/outcome_optimizer.py \
  --database /tmp/reins-site-demo.duckdb \
  --output website/demo-report.json --dashboard --port 8766
```

Use a fresh database per demonstration. The generated JSON contains synthetic
aggregates only. The public site explains current empirical policy selection;
quantization and deployment search are marked as future expansion. The slider
illustrates quality tradeoffs with an explicitly relaxed 25 percentage-point
relative drop; the SDK defaults to zero quality drop. Local console links are
hidden when hosted outside localhost. No server deployment is performed here.

### Human-readable case report

The main case-page download is `reins-data-agent-report.pdf`. The JSON link is
explicitly labeled as raw developer data. After regenerating measured JSON, run:

```bash
python -m pip install reportlab
python benchmarks/render_data_agent_report.py
```

The renderer writes `output/pdf/reins-data-agent-report.pdf` and copies it into
`website/`. It does not call Gemini or change any measured result.

### Featured savings page

`cases.html` presents selected completed-benchmark examples with named baselines,
visual cost comparisons and business-readable outputs. The original frozen-policy
audits remain in `data-agent-results.json` and `experiment-audit.html` unchanged.

```bash
PYTHONPATH=src:benchmarks python benchmarks/featured_cases.py
python benchmarks/render_savings_report.py
```

The primary download is now the two-page visual `reins-savings-report.pdf`.
Selection validates 300/300 acceptance, no observed quality loss, p95 under 60s,
complete usage pricing and lower cost than the explicitly named baseline. This
retrospective presentation selection is not a new validation-trained router.

The featured page now covers all four workloads. Project and paper collection
compare rule-based Flash Lite/Flash selection against fixed Flash. Paper collection
is explicitly an offline comparison (p95 79.04s); it is not described as meeting
the original 60s gate. Overview cards show every workload before opening details.
The downloadable visual brief has four pages, one per workload.
