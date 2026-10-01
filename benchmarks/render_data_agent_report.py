"""Build the human-readable report from the recorded audit; no API calls."""

import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "website/data-agent-results.json").read_text())
ink = colors.HexColor("#233c2b")
muted = colors.HexColor("#596552")
pale = colors.HexColor("#e9eedf")
styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        name="TitleR",
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=32,
        textColor=ink,
        spaceAfter=20,
    )
)
styles.add(
    ParagraphStyle(
        name="HeadingR",
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=ink,
        spaceBefore=12,
        spaceAfter=8,
    )
)
styles.add(
    ParagraphStyle(
        name="BodyR", fontName="Helvetica", fontSize=10, leading=14, textColor=ink, spaceAfter=8
    )
)
styles.add(
    ParagraphStyle(
        name="SmallR", fontName="Helvetica", fontSize=8.5, leading=12, textColor=muted, spaceAfter=8
    )
)
styles.add(
    ParagraphStyle(name="CellR", fontName="Helvetica", fontSize=9, leading=12, textColor=ink)
)
styles.add(
    ParagraphStyle(
        name="LabelR",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=13,
        textColor=muted,
        spaceAfter=12,
    )
)
story = []


def p(text, style="BodyR"):
    return Paragraph(text, styles[style])


def add(text, style="BodyR"):
    story.append(p(text, style))


def table(headers, rows, widths):
    t = Table(
        [[p(str(v), "CellR") for v in row] for row in [headers] + rows],
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), pale),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce2d4")),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 12))


labels = {
    "fixed-flash": "Flash, one item per call",
    "fixed-lite": "Flash Lite, one item per call",
    "compact-4": "Flash Lite, batches of 4",
    "compact-16": "Flash Lite, batches of 16",
    "rule-cascade": "Rule-based model escalation",
    "incremental": "Changed fields only",
    "source-parser": "Deterministic source parser",
    "reflector-single": "Strands: single-reflector prompt",
    "reflector-multi": "Strands: multi-reflector prompt",
    "reins-frozen": "Reins frozen routing",
}
titles = {
    "invoices": "Invoice & order extraction",
    "projects": "Public project research",
    "papers": "Research metadata collection",
    "updates": "Incremental data refresh",
}
explain = {
    "invoices": (
        "Extract five invoice fields while ignoring stale drafts and injected footer instructions. Invoices are constructed test records.",
        "The frozen router accepted 270 of 300 records. Amount strings sometimes became numbers, which fails the required output format. It should not replace the comparator. Batches of 16 passed all cases, but the parser also passed without model calls.",
    ),
    "projects": (
        "Collect repository language, license, default branch and archive status from reformatted GitHub metadata.",
        "The frozen router passed all 300 cases, but missed the 60-second p95 gate and cost more than the parser. Some batched alternatives lost accuracy; larger batches are not automatically a better execution policy.",
    ),
    "papers": (
        "Collect title, author, publication year, DOI and publication type from reformatted Crossref records.",
        "The frozen router passed all 300 cases, but missed the latency gate and did not beat the parser on model cost. These structured records do not yet test the harder task of reading arbitrary research websites.",
    ),
    "updates": (
        "Refresh controlled changes to project records. Every policy receives the same previously verified state.",
        "Changed-fields-only execution passed 300 of 300 cases and cut estimated model cost by 72% versus one-item Flash Lite calls. The parser also passed with no model calls. The gain supports incremental execution, not a claim of exclusive technology.",
    ),
}
add("REINS / MEASURED EXPERIMENTS / 30 SEPTEMBER 2026", "LabelR")
add("What actually makes\ndata agents cheaper?".replace("\n", "<br/>"), "TitleR")
add("Four workloads. Real Gemini calls. Quality measured before cost.", "HeadingR")
add(
    "This report explains the recorded experiments in plain language. It compares execution policies on the same held-out tasks and separates useful savings from results that are not ready to adopt."
)
updates = next(w for w in report["workloads"] if w["id"] == "updates")
mm = {x["name"]: x["metrics"] for x in updates["policies"]}
saving = (1 - mm["incremental"]["cost_per_accepted"] / mm["fixed-lite"]["cost_per_accepted"]) * 100
add(f"{saving:.0f}% lower model cost for incremental refresh", "HeadingR")
add(
    "Sending only changed fields cost about <b>$0.00831 per 1,000 accepted results</b>, versus <b>$0.02971</b> for one-item Flash Lite execution. Both passed 300/300 held-out records. These are usage-based API estimates; optimization overhead is separate."
)
add("The strongest simple baseline still wins", "HeadingR")
add(
    "A deterministic parser passed all four workloads without a model call. CPU cost is not priced. These source records are structured enough to parse, so the experiments do not establish a proprietary advantage or superiority to Amazon AgentCore."
)
table(
    ["Workload", "Frozen router accepted", "Adoption result"],
    [
        [titles[w["id"]], f"{w['policies'][-1]['metrics']['accepted']}/300", "Hold"]
        for w in report["workloads"]
    ],
    [228, 108, 163],
)
add(
    "All four frozen routers remain unapproved: they either fail quality/latency gates or do not improve cost over the parser. A held-out test is an audit, not a chance to reselect the winner.",
    "SmallR",
)
story.append(PageBreak())
add("HOW TO READ THE RESULTS", "LabelR")
add("A fair comparison starts\nwith the task.".replace("\n", "<br/>"), "TitleR")
add("The experiment in six steps", "HeadingR")
for text in [
    "1. Freeze 2,000 input cases: four workloads, each with 100 development, 100 validation and 300 held-out test cases.",
    "2. Use development cases to inspect failure modes and adapt published Strands reflection methods to Gemini.",
    "3. Compare policies on validation cases. Freeze routing by input segment, with baseline fallback where evidence is sparse.",
    "4. Execute every policy on held-out cases. Evaluate exact reference fields; charge failed attempts and fallback calls too.",
    "5. Apply adoption gates: at least 95% observed acceptance, no observed quality drop, p95 total latency no higher than 60 seconds, complete priced usage, and strictly lower cost per accepted result.",
    "6. Report 36 additional traffic stress runs across single/concurrent and steady/burst arrivals. No browser click launches a paid API call.",
]:
    add(text)
add("What the table columns mean", "HeadingR")
add(
    "<b>Accepted:</b> outputs matching the required reference fields. <b>USD / 1k accepted:</b> total estimated model-call cost, including failures, divided by accepted outputs and multiplied by 1,000. <b>p95 total:</b> 95% of items finished within this time, including queueing. The main test submits 300 items in a burst."
)
add("What is included - and what is not", "HeadingR")
add(
    "Prices use standard Gemini text rates, with known cached-read tokens priced separately. They are estimates, not reconciled account charges. Common development/validation acquisition and method-specific reflection costs are reported separately. Missing billing information stays unresolved. CPU, human labeling and initial acquisition of previous update state are not monetized."
)
add(
    "Independent workloads overlapped on the same provider account. Latency is observational, not proof of a causal speedup. Public metadata was reformatted and stress conditions constructed; this is not customer production traffic.",
    "SmallR",
)
for w in report["workloads"]:
    story.append(PageBreak())
    add("WORKLOAD / " + w["id"].upper(), "LabelR")
    add(titles[w["id"]], "TitleR")
    add(explain[w["id"]][0])
    rows = []
    for policy in w["policies"]:
        m = policy["metrics"]
        cost = m.get("cost_per_accepted")
        rows.append(
            [
                labels[policy["name"]],
                f"{m['accepted']}/{m['cases']}",
                "Unsettled" if cost is None else f"${cost * 1000:.5f}",
                f"{m['p95_ms'] / 1000:.2f}s",
            ]
        )
    table(
        ["Execution policy", "Accepted", "USD / 1k\naccepted", "p95 total"],
        rows,
        [235, 77, 104, 83],
    )
    add("What this means", "HeadingR")
    add(explain[w["id"]][1])
    reasonmap = {
        "quality_gate_failed": "quality below the required acceptance bar",
        "latency_gate_failed": "p95 latency above 60 seconds",
        "no_cost_improvement": "no cost improvement over the parser",
        "incomplete_costs": "incomplete cost information",
    }
    reasons = "; ".join(reasonmap.get(x, x.replace("_", " ")) for x in w["release_gate"]["reasons"])
    add("<b>Adoption decision: hold.</b> " + escape(reasons) + ".")
    add("Optimization and comparison context", "HeadingR")
    add(
        f"Shared development/validation acquisition: approximately <b>${w['shared_evaluation_cost']:.5f}</b>. This is additional to the per-result execution costs above."
    )
    refl = []
    for r in w["reflectors"]:
        state = "completed" if r["status"] == "complete" else "failed to produce an accepted prompt"
        refl.append(f"{r['kind'].title()} reflector: {state}; {r['model_calls']} model calls")
    add(escape(". ".join(refl)) + ".", "SmallR")
    add(
        "Strands methods were adapted to the same Gemini provider; no managed AWS service was benchmarked. Failed adaptations are not evidence of inferior AWS performance. The online case page contains traffic results, example outputs and overhead amortization.",
        "SmallR",
    )
    add(
        '<link href="https://47.245.114.167:8443/cases.html" color="#233c2b">Open the interactive case report</link> | <link href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization.html" color="#233c2b">AgentCore documentation</link> | <link href="https://ai.google.dev/gemini-api/docs/pricing" color="#233c2b">Gemini pricing</link>',
        "SmallR",
    )


def frame(c, doc):
    c.setFillColor(muted)
    c.setFont("Helvetica", 8)
    c.drawString(48, 29, "REINS  /  DATA-AGENT EXPERIMENT REPORT")
    c.drawRightString(547, 29, str(doc.page))
    c.setStrokeColor(pale)
    c.line(48, 44, 547, 44)


out = ROOT / "output/pdf/reins-data-agent-report.pdf"
SimpleDocTemplate(
    str(out),
    pagesize=(595, 842),
    leftMargin=48,
    rightMargin=48,
    topMargin=49,
    bottomMargin=62,
    title="Reins - Data Agent Experiment Report",
    author="Reins",
).build(story, onFirstPage=frame, onLaterPages=frame)
(ROOT / "website/reins-data-agent-report.pdf").write_bytes(out.read_bytes())
print(out)
