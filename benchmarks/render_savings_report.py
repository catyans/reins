"""Visual case brief from recorded, explicitly named comparisons."""

import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

R = Path(__file__).resolve().parents[1]
data = json.loads((R / "website/featured-cases.json").read_text())
ink = colors.HexColor("#233c2b")
muted = colors.HexColor("#64715d")
pale = colors.HexColor("#e9eedf")
green = colors.HexColor("#3a653a")
out = R / "output/pdf/reins-savings-report.pdf"
c = canvas.Canvas(str(out), pagesize=(595, 842))
c.setTitle("Reins - Measured Savings")
c.setAuthor("Reins")


def text(x, y, s, size=11, bold=False, color=ink):
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, s)


def para(s, x, y, w=499, size=10):
    p = Paragraph(
        s,
        ParagraphStyle("p", fontName="Helvetica", fontSize=size, leading=size * 1.5, textColor=ink),
    )
    a, h = p.wrap(w, 500)
    p.drawOn(c, x, y - h)
    return y - h


for page, case in enumerate(data["cases"], 1):
    b = next(p["metrics"] for p in case["policies"] if p["id"] == case["baseline"])
    m = case["metrics"]
    text(48, 790, "REINS / MEASURED SAVINGS / SEPTEMBER 2026", 9, True, muted)
    text(48, 738, case["title"], 28, True)
    para(case["description"], 48, 715)
    text(48, 643, f"{case['saving'] * 100:.1f}%", 56, True, green)
    text(48, 614, "lower API cost per accepted result", 13)
    for i, (label, value) in enumerate(
        [("Accepted test records", "300/300"), ("p95 total time", f"{m['p95_ms'] / 1000:.2f}s")]
    ):
        x = 332 + i * 116
        text(x, 657, value, 25, True)
        para(label, x, 642, w=104, size=9)
    text(48, 558, "Same task. Less model spend.", 18, True)
    for y, label, value, ratio in [
        (505, "Before: one item per call", b["cost_per_accepted"] * 1000, 1),
        (
            410,
            "After: " + case["execution_label"],
            m["cost_per_accepted"] * 1000,
            1 - case["saving"],
        ),
    ]:
        text(48, y, label, 11, True)
        text(418, y, f"${value:.5f}", 16, True)
        c.setFillColor(pale)
        c.roundRect(48, y - 37, 499, 16, 8, fill=1, stroke=0)
        c.setFillColor(green)
        c.roundRect(48, y - 37, 499 * ratio, 16, 8, fill=1, stroke=0)
    text(48, 345, "Estimated model cost per 1,000 accepted results", 9, False, muted)
    text(48, 303, "How it works", 18, True)
    stages = (
        ["Compare changed fields", "Reuse verified data", "Process differences"]
        if case["id"] == "updates"
        else (
            ["Read source records", "Select model + batch", "Validate and repair"]
            if case["policy"] == "rule-cascade"
            else ["Collect invoices", "Batch 16 together", "Validate each answer"]
        )
    )
    for i, s in enumerate(stages):
        x = 48 + i * 171
        c.setFillColor(pale)
        c.roundRect(x, 237, 157, 43, 7, fill=1, stroke=0)
        text(x + 10, 254, s, 9, True)
        if i < 2:
            text(x + 159, 252, ">", 11)
    if case["id"] == "updates":
        batch = next(p["metrics"] for p in case["policies"] if p["id"] == "compact-4")
        para(
            f"Also <b>{(1 - m['cost_per_accepted'] / batch['cost_per_accepted']) * 100:.1f}% lower cost than four-item batching</b>, with 300/300 accepted for both.",
            48,
            216,
        )
    elif case["id"] in ("projects", "papers"):
        para(
            f"300/300 accepted. p95 total: {b['p95_ms'] / 1000:.2f}s before, {m['p95_ms'] / 1000:.2f}s after. {case['timing_note']}",
            48,
            216,
            size=9,
        )
    else:
        para(
            "The batched configuration accepted 300/300 records, compared with 295/300 for one-item execution.",
            48,
            216,
        )
    para(
        f"Selected examples from completed benchmarks, compared with {case['baseline_model']} on the same 300 inputs. Constructed invoices and reformatted public metadata; p95 includes queueing. API list-price estimates, not billing debits.",
        48,
        169,
        size=9,
    )
    para(
        f"Shared development/validation acquisition: ${case['shared_evaluation_cost']:.5f}, additional to execution costs. CPU and labeling are not priced. Structured inputs also support deterministic parsing. Results are measured examples, not guaranteed production savings.",
        48,
        114,
        size=9,
    )
    text(48, 32, "reins / outcome-based execution optimization", 8, False, muted)
    text(535, 32, str(page), 8)
    c.showPage()
c.save()
(R / "website/reins-savings-report.pdf").write_bytes(out.read_bytes())
print(out)
