"""Agent Replay: step-by-step terminal replay of agent execution."""

from __future__ import annotations

import sys
import time
from typing import Any

import click

# ANSI codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"

_ICONS = {
    "llm": "🤖",
    "tool": "🔧",
    "retrieval": "🔍",
    "custom": "⚙️",
}


def replay_run(run: dict[str, Any], spans: list[dict[str, Any]], auto: bool = False) -> None:
    """Interactive step-by-step replay of an agent run.

    Press Enter to advance, 'q' to quit, 'a' for auto-play.
    """
    agent = run.get("agent_name", "unknown")
    status = run.get("status", "unknown")
    total_cost = run.get("total_cost", 0) or 0

    click.echo()
    click.echo(f"  {_BOLD}{'─' * 70}{_RESET}")
    click.echo(f"  {_BOLD}🎬 Agent Replay{_RESET}")
    click.echo(f"  Agent: {_CYAN}{agent}{_RESET}  |  Run: {run.get('run_id', '')[:10]}")
    click.echo(f"  Status: {_status_color(status)}{status}{_RESET}  |  Total Cost: ${total_cost:.4f}")
    click.echo(f"  Spans: {len(spans)}")
    click.echo(f"  {_BOLD}{'─' * 70}{_RESET}")
    click.echo(f"  {_DIM}[Enter] next step  [a] auto-play  [q] quit{_RESET}")
    click.echo()

    if not spans:
        click.echo(f"  {_DIM}No spans to replay.{_RESET}")
        return

    cumulative_cost = 0.0
    autoplay = auto

    for i, span in enumerate(spans):
        if not autoplay:
            try:
                key = click.getchar()
                if key == 'q':
                    click.echo(f"\n  {_DIM}Replay stopped.{_RESET}\n")
                    return
                if key == 'a':
                    autoplay = True
            except (EOFError, KeyboardInterrupt):
                return
        else:
            time.sleep(0.3)

        cost = span.get("cost", 0) or 0
        cumulative_cost += cost
        _render_step(i + 1, len(spans), span, cumulative_cost)

    # Final summary
    click.echo()
    click.echo(f"  {_BOLD}{'─' * 70}{_RESET}")
    click.echo(f"  {_BOLD}Replay Complete{_RESET}")
    click.echo(f"  Total: {len(spans)} steps  |  Cost: ${cumulative_cost:.4f}")

    degraded = sum(1 for s in spans if s.get("degraded"))
    errors = sum(1 for s in spans if s.get("status") == "error")
    if degraded:
        click.echo(f"  {_YELLOW}⚠ {degraded} call(s) degraded{_RESET}")
    if errors:
        click.echo(f"  {_RED}✗ {errors} error(s){_RESET}")
    click.echo()


def _render_step(
    step: int, total: int, span: dict[str, Any], cumulative_cost: float
) -> None:
    """Render a single replay step."""
    span_type = span.get("span_type", "custom")
    icon = _ICONS.get(span_type, "⚙️")
    name = span.get("name", "unknown")
    status = span.get("status", "ok")
    model = span.get("model", "")
    model_req = span.get("model_requested", "")
    degraded = span.get("degraded", False)
    tokens_in = span.get("tokens_in", 0) or 0
    tokens_out = span.get("tokens_out", 0) or 0
    cost = span.get("cost", 0) or 0
    duration = span.get("duration_ms", 0) or 0
    tool_name = span.get("tool_name")
    error_msg = span.get("error_message")
    context_health = span.get("context_health")

    # Step header
    progress = f"[{step}/{total}]"
    click.echo(f"  {_BOLD}{progress}{_RESET} {icon} {_CYAN}{name}{_RESET}")

    # Details
    indent = "       "
    if span_type == "llm":
        if degraded and model_req and model != model_req:
            click.echo(f"{indent}Model: {_YELLOW}{model_req} → {model} (degraded){_RESET}")
        elif model:
            click.echo(f"{indent}Model: {model}")

        if tokens_in or tokens_out:
            click.echo(f"{indent}Tokens: {tokens_in} in → {tokens_out} out")

    elif span_type == "tool" and tool_name:
        click.echo(f"{indent}Tool: {_MAGENTA}{tool_name}{_RESET}")
        tool_status = span.get("tool_status", "")
        if tool_status:
            color = _GREEN if tool_status == "success" else _RED
            click.echo(f"{indent}Result: {color}{tool_status}{_RESET}")

    if duration:
        dur_str = f"{duration:.0f}ms" if duration < 1000 else f"{duration / 1000:.1f}s"
        click.echo(f"{indent}Duration: {dur_str}")

    if cost:
        click.echo(f"{indent}Cost: ${cost:.6f}  (cumulative: ${cumulative_cost:.4f})")

    if context_health is not None:
        bar = _health_bar(context_health)
        click.echo(f"{indent}Context Health: {bar}")

    if status == "error" and error_msg:
        click.echo(f"{indent}{_RED}Error: {error_msg[:80]}{_RESET}")

    click.echo()


def _status_color(status: str) -> str:
    if status in ("ok", "completed"):
        return _GREEN
    if status in ("error", "failed"):
        return _RED
    if status == "running":
        return _YELLOW
    return _RESET


def _health_bar(score: float) -> str:
    blocks = int(score * 10)
    if score >= 0.7:
        color = _GREEN
    elif score >= 0.4:
        color = _YELLOW
    else:
        color = _RED
    return f"{color}{'█' * blocks}{'░' * (10 - blocks)}{_RESET} {score:.2f}"
