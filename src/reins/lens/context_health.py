"""Context Health Score: detect and measure context rot in agent runs."""

from __future__ import annotations

from typing import Any

# ANSI codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

# Default context window sizes by model family
_MODEL_CONTEXT_WINDOWS = {
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4": 200_000,
    "claude-3": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "o3": 200_000,
    "gemini-2.5": 1_000_000,
    "gemini-2.0": 1_000_000,
}


def get_context_window(model: str) -> int:
    """Get the context window size for a model."""
    for prefix, size in _MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return 200_000  # Default


def compute_health_scores(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute context health scores for each span in a run.

    Metrics:
    1. Token utilization ratio (context_tokens / context_window)
    2. Cumulative token growth rate
    3. Output token efficiency (output / input ratio trend)
    4. Duplication signal (repeated similar token counts)

    Returns spans enriched with computed health scores.
    """
    if not spans:
        return spans

    enriched = []
    cumulative_tokens = 0
    prev_tokens_out = 0
    output_ratios: list[float] = []
    token_history: list[int] = []

    for i, span in enumerate(spans):
        tokens_in = span.get("tokens_in", 0) or 0
        tokens_out = span.get("tokens_out", 0) or 0
        model = span.get("model", "") or ""
        context_window = get_context_window(model)

        # Track cumulative context usage
        cumulative_tokens += tokens_in + tokens_out
        token_history.append(tokens_in)

        # 1. Utilization score (higher utilization = lower health)
        utilization = tokens_in / context_window if context_window else 0
        if utilization < 0.3:
            utilization_score = 1.0
        elif utilization < 0.6:
            utilization_score = 1.0 - (utilization - 0.3) * 1.5
        else:
            utilization_score = max(0.05, 1.0 - utilization * 1.2)

        # 2. Output efficiency (declining output efficiency = rot)
        if tokens_in > 0:
            output_ratio = tokens_out / tokens_in
            output_ratios.append(output_ratio)
        efficiency_score = 1.0
        if len(output_ratios) >= 3:
            recent = output_ratios[-3:]
            older = output_ratios[:-3] if len(output_ratios) > 3 else output_ratios[:1]
            avg_recent = sum(recent) / len(recent)
            avg_older = sum(older) / len(older) if older else avg_recent
            if avg_older > 0 and avg_recent < avg_older * 0.5:
                efficiency_score = 0.5
            elif avg_older > 0 and avg_recent < avg_older * 0.7:
                efficiency_score = 0.7

        # 3. Duplication signal (repeated similar input sizes = potential loop)
        duplication_score = 1.0
        if len(token_history) >= 4:
            last_four = token_history[-4:]
            variance = _variance(last_four)
            mean = sum(last_four) / len(last_four)
            if mean > 0 and variance / mean < 0.05:
                # Very similar input sizes — possible repetition
                duplication_score = 0.6

        # Composite score (weighted average)
        health = (
            utilization_score * 0.5
            + efficiency_score * 0.3
            + duplication_score * 0.2
        )
        health = max(0.0, min(1.0, health))

        enriched_span = dict(span)
        enriched_span["_health_score"] = round(health, 3)
        enriched_span["_utilization"] = round(utilization, 3)
        enriched_span["_efficiency_score"] = round(efficiency_score, 3)
        enriched_span["_duplication_score"] = round(duplication_score, 3)
        enriched_span["_cumulative_tokens"] = cumulative_tokens
        enriched.append(enriched_span)

        prev_tokens_out = tokens_out

    return enriched


def _variance(values: list[int]) -> float:
    if not values:
        return 0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def render_health_report(run: dict[str, Any], spans: list[dict[str, Any]]) -> str:
    """Render a context health report with ASCII health curve."""
    enriched = compute_health_scores(spans)
    if not enriched:
        return "No spans to analyze."

    lines = []
    agent = run.get("agent_name", "unknown")
    lines.append("")
    lines.append(f"  {_BOLD}Context Health Report{_RESET}")
    lines.append(f"  Agent: {agent}  |  Run: {run.get('run_id', '')[:10]}")
    lines.append(f"  {'─' * 70}")

    # Health curve (ASCII sparkline)
    scores = [s["_health_score"] for s in enriched]
    lines.append("")
    lines.append(f"  {_BOLD}Health Curve{_RESET}  (1.0 = healthy, 0.0 = degraded)")
    lines.append("")
    lines.append(_render_ascii_chart(scores, width=60, height=10))
    lines.append("")

    # Per-span breakdown
    lines.append(f"  {_BOLD}Per-Span Breakdown{_RESET}")
    lines.append(f"  {'─' * 70}")
    lines.append(
        f"  {'#':>3} {'Model':<28} {'Tokens':>12} {'Util':>6} "
        f"{'Effic':>6} {'Dup':>5} {'Health':>8} {'Bar'}"
    )
    lines.append(f"  {'─' * 70}")

    for i, s in enumerate(enriched):
        model = (s.get("model", "") or "")[:26]
        tokens_in = s.get("tokens_in", 0) or 0
        tokens_out = s.get("tokens_out", 0) or 0
        health = s["_health_score"]
        util = s["_utilization"]
        eff = s["_efficiency_score"]
        dup = s["_duplication_score"]

        bar = _health_bar_mini(health)
        color = _GREEN if health >= 0.7 else (_YELLOW if health >= 0.4 else _RED)

        lines.append(
            f"  {i + 1:>3} {model:<28} {tokens_in:>5}→{tokens_out:<5} "
            f"{util:>5.1%} {eff:>5.2f} {dup:>5.2f} "
            f"{color}{health:>7.3f}{_RESET} {bar}"
        )

    # Summary
    lines.append(f"  {'─' * 70}")
    avg_health = sum(scores) / len(scores)
    min_health = min(scores)
    color = _GREEN if avg_health >= 0.7 else (_YELLOW if avg_health >= 0.4 else _RED)

    lines.append(f"  Avg Health: {color}{avg_health:.3f}{_RESET}  |  Min: {min_health:.3f}")

    # Warnings
    rot_start = None
    for i, score in enumerate(scores):
        if score < 0.5 and rot_start is None:
            rot_start = i + 1

    if rot_start:
        lines.append(f"  {_YELLOW}⚠ Context rot detected starting at span #{rot_start}{_RESET}")
    elif avg_health < 0.7:
        lines.append(f"  {_YELLOW}⚠ Context health is below optimal{_RESET}")
    else:
        lines.append(f"  {_GREEN}✓ Context health is good{_RESET}")

    lines.append("")
    return "\n".join(lines)


def _render_ascii_chart(values: list[float], width: int = 60, height: int = 10) -> str:
    """Render an ASCII chart of health scores over time."""
    if not values:
        return ""

    lines = []
    # Resample if needed
    if len(values) > width:
        step = len(values) / width
        resampled = [values[int(i * step)] for i in range(width)]
    else:
        resampled = values

    for row in range(height, 0, -1):
        threshold = row / height
        line = "  "
        if row == height:
            line += "1.0│"
        elif row == height // 2:
            line += "0.5│"
        else:
            line += "   │"

        for v in resampled:
            if v >= threshold:
                if v >= 0.7:
                    line += f"{_GREEN}█{_RESET}"
                elif v >= 0.4:
                    line += f"{_YELLOW}█{_RESET}"
                else:
                    line += f"{_RED}█{_RESET}"
            else:
                line += " "

        lines.append(line)

    # X axis
    lines.append("     └" + "─" * len(resampled))
    lines.append("      " + "1" + " " * (len(resampled) - 2) + str(len(values)))

    return "\n".join(lines)


def _health_bar_mini(score: float) -> str:
    """Mini health bar for table rows."""
    blocks = int(score * 8)
    if score >= 0.7:
        color = _GREEN
    elif score >= 0.4:
        color = _YELLOW
    else:
        color = _RED
    return f"{color}{'█' * blocks}{'░' * (8 - blocks)}{_RESET}"
