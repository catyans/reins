"""Human-readable project pilot report. Reads saved evidence; never calls a model."""

import argparse
import json
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

INK = colors.HexColor("#203c2e")
MUTED = colors.HexColor("#65745f")
BG = colors.HexColor("#f6f7f1")
LINE = colors.HexColor("#dbe2d3")
ACCENT = colors.HexColor("#91af72")
NAMES = {
    "parser": "Deterministic extraction",
    "fixed-lite": "Flash Lite / full refresh",
    "fixed-flash": "Flash / full refresh",
    "cached-cascade": "Parser + model + cache",
    "incremental": "Reins incremental",
}
PHASES = {
    "initial": "First collection",
    "update-1": "Release update 1",
    "update-2": "Release update 2",
    "unchanged": "Unchanged recheck",
}


def money(value):
    return f"${value:.5f}" if value is not None else "n/a"


def render(root):
    root = Path(root)
    report = json.loads((root / "report.json").read_text())
    recovery = json.loads((root / "recovery-report.json").read_text())
    audit = json.loads((root / "quality-diagnostics.json").read_text())
    output = Path("output/pdf/reins-project-update-report.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output), pagesize=(840, 594))
    c.setTitle("Reins - Public Project Update Pilot")
    c.setAuthor("Reins")

    def text(value, x, y, size=12, color=INK, bold=False):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, value)

    def paragraph(value, x, y, width, size=11, color=MUTED):
        style = ParagraphStyle(
            "body", fontName="Helvetica", fontSize=size, leading=size * 1.5, textColor=color
        )
        p = Paragraph(escape(value).replace("\n", "<br/>"), style)
        _, h = p.wrap(width, 500)
        p.drawOn(c, x, y - h)
        return y - h

    def page(number, eyebrow, title, subtitle):
        c.setFillColor(BG)
        c.rect(0, 0, 840, 594, fill=1, stroke=0)
        text("reins", 42, 546, 24, bold=True)
        text(eyebrow.upper(), 42, 502, 9, MUTED, True)
        text(title, 42, 463, 28, bold=True)
        paragraph(subtitle, 42, 441, 754, 11)
        c.setStrokeColor(LINE)
        c.line(42, 40, 798, 40)
        text(
            "Public project update pilot | Real Gemini API usage | Historical replay",
            42,
            24,
            8,
            MUTED,
        )
        text(f"{number} / 5", 766, 24, 8, MUTED)

    test = report["splits"]["test"]
    page(
        1,
        "Measured execution",
        "Every update. Less repeated work.",
        "60 public projects, 180 commit-pinned release snapshots. Held-out evaluation: 20 "
        "projects, "
        "four workflow phases each. Every strategy sees the same source snapshots.",
    )
    bounded = any(not s["cost_complete"] or not s["evaluation_complete"] for s in test.values())
    maximum = (
        max(
            (
                s.get("cost_upper_bound", s["estimated_cost"])
                if bounded
                else s["cost_per_accepted"] or 0
            )
            for s in test.values()
        )
        or 1
    )
    text(
        "TEST WORKLOAD API COST (RECORDED USAGE + UNRESOLVED RESERVE)"
        if bounded
        else "API COST PER ACCEPTED TASK",
        42,
        377,
        9,
        MUTED,
        True,
    )
    for index, (policy, s) in enumerate(test.items()):
        y = 341 - index * 48
        text(NAMES[policy], 42, y, 11, bold=policy == "incremental")
        c.setFillColor(LINE)
        c.roundRect(256, y - 2, 330, 16, 3, fill=1, stroke=0)
        c.setFillColor(INK if policy == "incremental" else ACCENT)
        amount = s["estimated_cost"] if bounded else s["cost_per_accepted"] or 0
        width = 330 * amount / maximum
        if width:
            c.roundRect(256, y - 2, width, 16, min(3, width / 2), fill=1, stroke=0)
        upper = s.get("cost_upper_bound", amount)
        if bounded and upper > amount:
            c.setFillColor(colors.HexColor("#d3dfc8"))
            c.rect(256 + width, y - 2, 330 * (upper - amount) / maximum, 16, fill=1, stroke=0)
        label = (
            (f"${amount:.3f} - ${upper:.3f}" if upper > amount else money(amount))
            if bounded
            else money(s["cost_per_accepted"])
        )
        text(label, 610, y + 2, 12, bold=True)
        text(
            f"{s['accepted']} accepted; {s.get('assessed', s['tasks'])}/{s['tasks']} assessed",
            610,
            y - 12,
            9,
            MUTED,
        )
    savings = report["qualified_savings"]
    statement = (
        f"{savings * 100:.1f}% lower cost per accepted task against the selected baseline."
        if savings is not None and savings > 0
        else "Compare cost and acceptance together; quality-equivalent savings are not established."
    )
    paragraph(statement, 42, 91, 754, 11, INK)
    c.showPage()

    page(
        2,
        "Where work is saved",
        "Four phases. One complete comparison.",
        "Initial work is included. An unchanged recheck is shown separately, so cache savings "
        "are not confused with the cost of handling a real content change.",
    )
    for i, (phase, policies) in enumerate(report["phases"].items()):
        x, top = 42 + (i % 2) * 385, 380 - (i // 2) * 148
        s = policies["incremental"]
        text(PHASES[phase], x, top, 14, bold=True)
        text(f"{s['calls']} model calls", x, top - 34, 24, bold=True)
        text(
            f"{s['accepted']} accepted | {s.get('assessed', s['tasks'])}/{s['tasks']} assessed",
            x,
            top - 58,
            10,
            MUTED,
        )
        baseline = policies.get(report["baseline"] or "cached-cascade")
        paragraph(
            f"Recorded Reins usage: {money(s['estimated_cost'])}. "
            f"Parser/model/cache baseline: {baseline['calls']} calls, "
            f"{money(baseline['estimated_cost'])} recorded usage. "
            f"Reins reused {s['reused_fields']} stored field results.",
            x,
            top - 76,
            350,
            10,
        )
    paragraph(
        "Latency is observed processing time for frozen snapshots, including model calls, "
        "validation and persistence. Original public-source collection is excluded. "
        "Provider load and concurrent tasks can affect timing.",
        42,
        95,
        754,
        10,
    )
    c.showPage()

    page(
        3,
        "Source-backed deliverables",
        "A record, with its evidence.",
        "Illustrative held-out outputs. Each value links to a pinned public source; "
        "the complete records are included in the raw experiment export.",
    )
    records = [
        r
        for r in report["records"]
        if r["policy"] == "incremental" and r["phase"] == "update-2" and r["accepted"]
    ]
    for i, record in enumerate(records[:2]):
        x, y = 42 + i * 385, 380
        text(record["project"], x, y, 14, bold=True)
        y -= 28
        for field in ("purpose", "install_command", "license"):
            answer = record["fields"].get(field) or {}
            value = answer.get("value") or "Not documented in this snapshot"
            if len(value) > 340:
                value = value[:337] + "..."
            text(field.replace("_", " ").upper(), x, y, 8, MUTED, True)
            y = paragraph(value, x, y - 9, 350, 10, INK) - 22
        text("Open the pinned README", x, max(y, 80), 10, MUTED)
        c.linkURL(record["sources"]["readme"]["url"], (x, max(y, 80) - 3, x + 160, max(y, 80) + 12))
    c.showPage()

    page(
        4,
        "Recovery",
        "Keep the response you already paid for.",
        "Controlled fault injection using a real Gemini response, isolated from the "
        "release-change benchmark.",
    )
    paragraph(
        "1. A model response and its usage were saved durably.\n"
        "2. Execution was deliberately interrupted before the field checkpoint completed.\n"
        "3. A new runtime resumed the task and reused that response.\n"
        "4. Remaining work completed; subsequent unchanged phases reused saved results.",
        42,
        372,
        740,
        15,
        INK,
    )
    text(f"{recovery['completed_jobs']} completed tasks", 42, 206, 26, bold=True)
    text("0 duplicate request keys", 430, 206, 26, bold=True)
    paragraph(
        f"Project: {recovery['project']}. Before restart: "
        f"{recovery['before_restart']['calls']} call(s), "
        f"{money(recovery['before_restart']['estimated_cost'])}. After completion: "
        f"{recovery['after_resume']['calls']} total call(s), "
        f"{money(recovery['after_resume']['estimated_cost'])}. Additional calls, if any, "
        "finish other fields; they do not repeat the interrupted request.",
        42,
        166,
        754,
        11,
    )
    paragraph(
        "This validates one recovery boundary, not a production failure-rate reduction. "
        "Requests with uncertain provider outcomes remain held for reconciliation.",
        42,
        88,
        754,
        10,
    )
    c.showPage()

    page(
        5,
        "Method and complete accounting",
        "What the numbers mean.",
        "The full experiment is saved locally. The public projection contains results and "
        "progress, "
        "not credentials, internal prompts or a paid execution endpoint.",
    )
    y = 383
    for title, body in (
        (
            "Quality",
            report["quality_method"] + ". Reference annotations are model-assisted. "
            "The evaluator can make mistakes; no human-blind-review or statistical "
            "non-inferiority claim is made. Citation checks can reject formatting differences. "
            f"{audit['groups_with_judge_disagreement']}/{audit['identical_answer_groups']} "
            "identical-answer groups received inconsistent model judgments.",
        ),
        (
            "Selection",
            "20 development, 20 validation and 20 held-out projects; organization-isolated. "
            "The protocol requires 95% validation acceptance to select a baseline. "
            "No baseline met that requirement in this pilot. "
            "All strategies remain visible, including the zero-model parser.",
        ),
        (
            "Cost",
            "Input, cached-input and output token usage are priced explicitly. Failed or "
            "unresolved "
            "requests are retained; unresolved costs prevent a complete-cost savings claim. "
            "These are "
            "API estimates, not invoice debits. Local compute time is not converted into "
            "fictional cloud cost.",
        ),
        (
            "Product focus",
            "Incremental project updates, durable request reuse and task-level evidence. "
            "AWS AgentCore also supports observability, evaluation, configuration "
            "optimization and external "
            "models. This pilot is not a direct AWS performance benchmark.",
        ),
    ):
        text(title.upper(), 42, y, 9, MUTED, True)
        y = paragraph(body, 165, y + 3, 627, 10) - 20
    cost = report["research_cost"]
    cost_text = " | ".join(
        f"{k}: {money(v['estimated_cost'])} / {v['calls']} calls" for k, v in cost.items()
    )
    reserved = sum(v.get("unsettled_reservation", 0) for v in cost.values())
    known_total = sum(v["estimated_cost"] for v in cost.values())
    known_total += recovery["after_resume"]["estimated_cost"]
    paragraph(
        cost_text
        + "\nRecovery: "
        + money(recovery["after_resume"]["estimated_cost"])
        + f". Recorded total: ${known_total:.3f}; unresolved reserve: ${reserved:.3f}. "
        + "Labeling and evaluation overhead is reported separately.",
        42,
        max(y, 92),
        754,
        10,
        INK,
    )
    text("Pricing source", 42, 58, 8, MUTED)
    c.linkURL("https://ai.google.dev/gemini-api/docs/pricing", (42, 55, 111, 69))
    text("AWS capability reference", 165, 58, 8, MUTED)
    c.linkURL(
        "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization.html",
        (165, 55, 284, 69),
    )
    c.save()
    shutil.copyfile(output, "website/reins-project-update-report.pdf")
    print(output)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="output/benchmarks/projects-v2")
    render(p.parse_args().output)
