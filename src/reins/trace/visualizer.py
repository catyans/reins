"""Terminal-based trace visualization: call trees, timelines, summaries."""

from __future__ import annotations

from typing import Any

# ANSI color codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"

# Span type icons
_ICONS = {
    "llm": "🤖",
    "tool": "🔧",
    "retrieval": "🔍",
    "custom": "⚙️",
}


def format_cost(cost: float) -> str:
    if cost < 0.001:
        return f"${cost:.6f}"
    if cost < 1:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"


def format_tokens(tokens_in: int, tokens_out: int) -> str:
    return f"{tokens_in}→{tokens_out} tok"


def _status_color(status: str) -> str:
    if status == "ok" or status == "completed":
        return _GREEN
    if status == "error" or status == "failed":
        return _RED
    if status == "running":
        return _YELLOW
    return _RESET


def _health_bar(score: float | None) -> str:
    """Render context health as a colored bar."""
    if score is None:
        return f"{_DIM}──{_RESET}"
    blocks = int(score * 10)
    if score >= 0.7:
        color = _GREEN
    elif score >= 0.4:
        color = _YELLOW
    else:
        color = _RED
    bar = "█" * blocks + "░" * (10 - blocks)
    return f"{color}{bar}{_RESET} {score:.2f}"


def render_run_list(runs: list[dict[str, Any]]) -> str:
    """Render a table of recent runs."""
    if not runs:
        return "No runs found."

    lines = []
    lines.append("")
    lines.append(f"  {_BOLD}Recent Agent Runs{_RESET}")
    lines.append(f"  {'─' * 80}")
    lines.append(
        f"  {'Run ID':<12} {'Agent':<18} {'Status':<12} {'Cost':>10} "
        f"{'Spans':>6} {'Degraded':>9} {'Started':<20}"
    )
    lines.append(f"  {'─' * 80}")

    for r in runs:
        status = r.get("status", "unknown")
        color = _status_color(status)
        run_id = r.get("run_id", "")[:10]
        agent = r.get("agent_name", "unknown")[:16]
        cost = format_cost(r.get("total_cost", 0) or 0)
        spans = r.get("span_count", 0)
        degraded = r.get("degraded_count", 0)
        started = r.get("started_at", "")[:19]

        lines.append(
            f"  {run_id:<12} {agent:<18} {color}{status:<12}{_RESET} "
            f"{cost:>10} {spans:>6} {degraded:>9} {started:<20}"
        )

    lines.append(f"  {'─' * 80}")
    lines.append("")
    return "\n".join(lines)


def render_call_tree(run: dict[str, Any], spans: list[dict[str, Any]]) -> str:
    """Render a call tree for a single run."""
    lines = []
    agent = run.get("agent_name", "unknown")
    status = run.get("status", "unknown")
    total_cost = format_cost(run.get("total_cost", 0) or 0)
    degraded = run.get("degraded_count", 0)

    # Header
    lines.append("")
    lines.append(f"  {_BOLD}Run: {run.get('run_id', '')[:10]}{_RESET}")
    lines.append(
        f"  Agent: {agent}  |  Status: {_status_color(status)}{status}{_RESET}"
        f"  |  Cost: {total_cost}"
    )
    if degraded:
        lines.append(f"  {_YELLOW}⚠ {degraded} call(s) were auto-degraded{_RESET}")
    lines.append(f"  {'─' * 80}")
    lines.append("")

    if not spans:
        lines.append("  No spans recorded.")
        lines.append("")
        return "\n".join(lines)

    # Build tree structure
    root_spans = [s for s in spans if not s.get("parent_span_id")]
    child_map: dict[str, list[dict]] = {}
    for s in spans:
        parent = s.get("parent_span_id")
        if parent:
            child_map.setdefault(parent, []).append(s)

    # If no hierarchy, just list them in order
    if not root_spans:
        root_spans = spans

    for i, span in enumerate(root_spans):
        is_last = i == len(root_spans) - 1
        _render_span(lines, span, child_map, prefix="  ", is_last=is_last)

    # Summary
    lines.append("")
    lines.append(f"  {'─' * 80}")
    total_tokens_in = sum(s.get("tokens_in", 0) or 0 for s in spans)
    total_tokens_out = sum(s.get("tokens_out", 0) or 0 for s in spans)
    total_duration = sum(s.get("duration_ms", 0) or 0 for s in spans)
    lines.append(
        f"  {_BOLD}Total:{_RESET} {len(spans)} spans  |  "
        f"{format_tokens(total_tokens_in, total_tokens_out)}  |  "
        f"{format_duration(total_duration)}  |  {total_cost}"
    )
    lines.append("")

    return "\n".join(lines)


def _render_span(
    lines: list[str],
    span: dict[str, Any],
    child_map: dict[str, list[dict]],
    prefix: str = "",
    is_last: bool = True,
) -> None:
    """Render a single span in the call tree."""
    connector = "└── " if is_last else "├── "
    span_type = span.get("span_type", "custom")
    icon = _ICONS.get(span_type, "⚙️")
    name = span.get("name", "unknown")
    status = span.get("status", "ok")
    color = _status_color(status)

    # Main line
    model = span.get("model", "")
    model_req = span.get("model_requested", "")
    degraded = span.get("degraded", False)
    cost = span.get("cost", 0) or 0
    duration = span.get("duration_ms", 0) or 0
    tokens_in = span.get("tokens_in", 0) or 0
    tokens_out = span.get("tokens_out", 0) or 0

    # Build description
    desc_parts = []
    if model:
        if degraded and model_req and model != model_req:
            desc_parts.append(f"{_YELLOW}{model_req}→{model}{_RESET}")
        else:
            desc_parts.append(f"{_CYAN}{model}{_RESET}")
    if tokens_in or tokens_out:
        desc_parts.append(format_tokens(tokens_in, tokens_out))
    if duration:
        desc_parts.append(format_duration(duration))
    if cost:
        desc_parts.append(format_cost(cost))

    # Context health
    health = span.get("context_health")
    if health is not None:
        desc_parts.append(f"health:{_health_bar(health)}")

    # Error
    if status == "error":
        err = span.get("error_message", "")
        if err:
            desc_parts.append(f"{_RED}{err[:60]}{_RESET}")

    # Tool info
    tool_name = span.get("tool_name")
    if tool_name:
        desc_parts.insert(0, f"{_MAGENTA}{tool_name}{_RESET}")

    desc = "  ".join(desc_parts)
    lines.append(f"{prefix}{connector}{icon} {color}{name}{_RESET}  {desc}")

    # Children
    span_id = span.get("span_id", "")
    children = child_map.get(span_id, [])
    child_prefix = prefix + ("    " if is_last else "│   ")
    for j, child in enumerate(children):
        _render_span(lines, child, child_map, child_prefix, j == len(children) - 1)


def render_span_detail(span: dict[str, Any]) -> str:
    """Render detailed view of a single span."""
    lines = []
    lines.append("")
    lines.append(f"  {_BOLD}Span: {span.get('span_id', '')[:10]}{_RESET}")
    lines.append(f"  {'─' * 60}")

    fields = [
        ("Type", span.get("span_type")),
        ("Name", span.get("name")),
        ("Provider", span.get("provider")),
        ("Model", span.get("model")),
        ("Model Requested", span.get("model_requested")),
        ("Degraded", span.get("degraded")),
        ("Status", span.get("status")),
        ("Tokens In", span.get("tokens_in")),
        ("Tokens Out", span.get("tokens_out")),
        ("Cost", format_cost(span.get("cost", 0) or 0)),
        ("Duration", format_duration(span.get("duration_ms", 0) or 0)),
        ("Context Tokens", span.get("context_tokens")),
        ("Context Health", span.get("context_health")),
        ("Error", span.get("error_message")),
        ("Started", span.get("started_at")),
        ("Ended", span.get("ended_at")),
    ]

    for label, value in fields:
        if value not in (None, "", False):
            lines.append(f"  {label:<20} {value}")

    lines.append("")
    return "\n".join(lines)
