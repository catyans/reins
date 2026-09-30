"""Render a deterministic README chart from the published case measurements."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render():
    payload = json.loads((ROOT / "docs/assets/featured-cases.json").read_text())
    cases = {c["id"]: c for c in payload["cases"]}
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 680" role="img">',
        "<title>Measured savings on selected Gemini benchmarks</title>",
        '<rect width="1100" height="680" rx="20" fill="#f6f7f0"/>',
        '<g font-family="Arial, sans-serif" fill="#193b2d">',
        (
            '<text x="48" y="65" font-size="32" font-weight="700">'
            "Less API spend. Every test record accepted.</text>"
        ),
        (
            '<text x="48" y="100" font-size="17">'
            "Cost per accepted result · Named baseline = 100 · Lower is better</text>"
        ),
    ]
    labels = {
        "updates": "Incremental data refresh",
        "papers": "Research paper collection",
        "projects": "Project research",
    }
    for i, key in enumerate(labels):
        case = cases[key]
        metrics = case["metrics"]
        base = next(p["metrics"] for p in case["policies"] if p["id"] == case["baseline"])
        saving = 1 - metrics["cost_per_accepted"] / base["cost_per_accepted"]
        assert metrics["accepted"] == metrics["cases"] == 300
        assert metrics["cost_complete"] and base["cost_complete"]
        assert abs(saving - case["saving"]) < 1e-10
        y = 156 + i * 142
        svg.extend(
            [
                f'<text x="48" y="{y}" font-size="21" font-weight="700">{labels[key]}</text>',
                (
                    f'<text x="48" y="{y + 29}" font-size="15">'
                    f"300/300 accepted · vs {html.escape(case['baseline_model'])}</text>"
                ),
                f'<rect x="475" y="{y - 17}" width="420" height="18" rx="5" fill="#d4dccf"/>',
                (
                    f'<rect x="475" y="{y + 12}" width="{420 * (1 - saving):.3f}"'
                    f' height="23" rx="5" fill="#2f6547"/>'
                ),
                f'<text x="925" y="{y + 5}" font-size="32" font-weight="700">{saving:.1%}</text>',
                f'<text x="925" y="{y + 29}" font-size="14">lower API cost</text>',
                (
                    f'<text x="475" y="{y + 58}" font-size="13">'
                    f"Baseline 100 → Selected policy {100 * (1 - saving):.1f}</text>"
                ),
            ]
        )
    svg.extend(
        [
            (
                '<text x="48" y="590" font-size="15">'
                "Real API calls on reformatted public metadata and controlled updates.</text>"
            ),
            (
                '<text x="48" y="615" font-size="15">'
                "Selected examples; not customer production traffic. "
                "Failed attempts and fallbacks included.</text>"
            ),
            (
                '<text x="48" y="640" font-size="15">'
                "Paper collection is offline (p95 79.04s). "
                "Complete comparisons and methods linked below.</text>"
            ),
            "</g></svg>",
        ]
    )
    (ROOT / "docs/assets/measured-savings.svg").write_text("\n".join(svg) + "\n")


if __name__ == "__main__":
    render()
