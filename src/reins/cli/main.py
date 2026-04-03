"""Reins CLI entry point."""

from __future__ import annotations

import json
import sys

import click

from reins.core.config import ReinsConfig
from reins.core.storage import Storage


def _get_storage() -> Storage:
    config = ReinsConfig.load()
    return Storage(config.storage_path)


@click.group()
@click.version_option(package_name="reins")
def cli() -> None:
    """Reins: Take control of your AI agents."""
    pass


@cli.command()
@click.option("--period", default="today", help="Time period: today, week, month, or date range")
@click.option("--agent", default=None, help="Filter by agent name")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "csv"]))
def report(period: str, agent: str | None, fmt: str) -> None:
    """Show cost report."""
    storage = _get_storage()

    where = "WHERE 1=1"
    if period == "today":
        where += " AND DATE(r.started_at) = CURRENT_DATE"
    elif period == "week":
        where += " AND r.started_at >= CURRENT_DATE - INTERVAL 7 DAY"
    elif period == "month":
        where += " AND r.started_at >= CURRENT_DATE - INTERVAL 30 DAY"

    if agent:
        where += f" AND r.agent_name = '{agent}'"

    # Summary by agent
    rows = storage.query(f"""
        SELECT
            r.agent_name,
            COUNT(DISTINCT r.run_id) as runs,
            COUNT(s.span_id) as calls,
            COALESCE(SUM(s.cost), 0) as total_cost,
            COALESCE(AVG(s.cost), 0) as avg_cost,
            SUM(CASE WHEN s.degraded THEN 1 ELSE 0 END) as degraded_count
        FROM runs r
        LEFT JOIN spans s ON r.run_id = s.run_id
        {where}
        GROUP BY r.agent_name
        ORDER BY total_cost DESC
    """)

    if fmt == "json":
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("No data for this period.")
        return

    # Table format
    total = sum(r["total_cost"] for r in rows)
    click.echo()
    click.echo(f"  Cost Report ({period})")
    click.echo(f"  {'─' * 60}")
    click.echo(f"  {'Agent':<20} {'Runs':>6} {'Calls':>6} {'Cost':>10} {'Avg':>10} {'Degraded':>9}")
    click.echo(f"  {'─' * 60}")

    for r in rows:
        click.echo(
            f"  {r['agent_name']:<20} {r['runs']:>6} {r['calls']:>6} "
            f"${r['total_cost']:>8.4f} ${r['avg_cost']:>8.4f} {r['degraded_count']:>9}"
        )

    click.echo(f"  {'─' * 60}")
    click.echo(f"  {'TOTAL':<20} {'':>6} {'':>6} ${total:>8.4f}")
    click.echo()


@cli.command()
@click.argument("sql")
def query(sql: str) -> None:
    """Execute a SQL query against the trace database."""
    storage = _get_storage()
    try:
        rows = storage.query(sql)
        if not rows:
            click.echo("No results.")
            return
        click.echo(json.dumps(rows, indent=2, default=str))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--port", default=8082, help="Proxy port")
@click.option("--host", default="localhost", help="Proxy host")
@click.option("--budget", default=None, help="Budget limit (e.g., '$5/day')")
@click.option("--on-exceed", default="alert", type=click.Choice(["degrade", "pause", "alert", "reject"]))
def proxy(port: int, host: str, budget: str | None, on_exceed: str) -> None:
    """Start the local transparent proxy server."""
    try:
        from aiohttp import web
    except ImportError:
        click.echo("Proxy requires aiohttp. Install with: pip install reins[proxy]", err=True)
        sys.exit(1)

    from reins.proxy.server import create_proxy_app

    app = create_proxy_app(budget=budget, on_exceed=on_exceed)

    click.echo(f"Starting Reins proxy at http://{host}:{port}")
    click.echo(f"Budget: {budget or 'unlimited'}  |  On exceed: {on_exceed}")
    click.echo()
    click.echo("To use with Claude Code:")
    click.echo(f"  export ANTHROPIC_BASE_URL=http://{host}:{port}")
    click.echo()

    web.run_app(app, host=host, port=port, print=None)


# ─── Trace commands ──────────────────────────────────────────────────────────

@cli.group()
def trace() -> None:
    """Trace inspection commands."""
    pass


@trace.command("list")
@click.option("--limit", default=20, help="Number of recent runs to show")
@click.option("--agent", default=None, help="Filter by agent name")
def trace_list(limit: int, agent: str | None) -> None:
    """List recent agent runs."""
    from reins.trace.visualizer import render_run_list

    storage = _get_storage()
    where = "WHERE 1=1"
    if agent:
        where += f" AND r.agent_name = '{agent}'"

    rows = storage.query(f"""
        SELECT
            r.run_id, r.agent_name, r.status, r.total_cost,
            r.degraded_count, r.started_at,
            COUNT(s.span_id) as span_count
        FROM runs r
        LEFT JOIN spans s ON r.run_id = s.run_id
        {where}
        GROUP BY r.run_id, r.agent_name, r.status, r.total_cost,
                 r.degraded_count, r.started_at
        ORDER BY r.started_at DESC
        LIMIT {limit}
    """)
    click.echo(render_run_list(rows))


@trace.command("show")
@click.argument("run_id")
def trace_show(run_id: str) -> None:
    """Show call tree for a specific run."""
    from reins.trace.visualizer import render_call_tree

    storage = _get_storage()

    # Support partial run_id match
    runs = storage.query(f"""
        SELECT * FROM runs WHERE run_id LIKE '{run_id}%' LIMIT 1
    """)
    if not runs:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    run = runs[0]
    full_id = run["run_id"]

    spans = storage.query(f"""
        SELECT * FROM spans WHERE run_id = '{full_id}'
        ORDER BY started_at ASC
    """)

    click.echo(render_call_tree(run, spans))


@trace.command("spans")
@click.argument("run_id")
def trace_spans(run_id: str) -> None:
    """Show detailed span list for a run."""
    from reins.trace.visualizer import render_span_detail

    storage = _get_storage()
    spans = storage.query(f"""
        SELECT * FROM spans WHERE run_id LIKE '{run_id}%'
        ORDER BY started_at ASC
    """)
    if not spans:
        click.echo(f"No spans found for run: {run_id}", err=True)
        sys.exit(1)

    for span in spans:
        click.echo(render_span_detail(span))


@trace.command("export")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "otel"]))
@click.option("--run-id", default=None, help="Export specific run")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
def trace_export(fmt: str, run_id: str | None, output: str | None) -> None:
    """Export traces."""
    storage = _get_storage()

    if run_id:
        spans = storage.query(f"SELECT * FROM spans WHERE run_id LIKE '{run_id}%' ORDER BY started_at")
    else:
        spans = storage.query("SELECT * FROM spans ORDER BY started_at DESC LIMIT 1000")

    if fmt == "json":
        data = json.dumps(spans, indent=2, default=str)
    elif fmt == "otel":
        from reins.trace.exporter import spans_to_otlp_json
        data = spans_to_otlp_json(spans)

    if output:
        with open(output, "w") as f:
            f.write(data)
        click.echo(f"Exported {len(spans)} spans to {output}")
    else:
        click.echo(data)


# ─── Lens commands ───────────────────────────────────────────────────────────

@cli.command()
@click.argument("run_id")
def replay(run_id: str) -> None:
    """Step-by-step replay of an agent run."""
    from reins.lens.replay import replay_run

    storage = _get_storage()
    runs = storage.query(f"SELECT * FROM runs WHERE run_id LIKE '{run_id}%' LIMIT 1")
    if not runs:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    run = runs[0]
    spans = storage.query(f"""
        SELECT * FROM spans WHERE run_id = '{run['run_id']}'
        ORDER BY started_at ASC
    """)

    replay_run(run, spans)


@cli.command()
@click.argument("run_id")
def health(run_id: str) -> None:
    """Show context health curve for a run."""
    from reins.lens.context_health import render_health_report

    storage = _get_storage()
    runs = storage.query(f"SELECT * FROM runs WHERE run_id LIKE '{run_id}%' LIMIT 1")
    if not runs:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    spans = storage.query(f"""
        SELECT * FROM spans WHERE run_id = '{runs[0]['run_id']}'
        ORDER BY started_at ASC
    """)

    click.echo(render_health_report(runs[0], spans))


# ─── Config ──────────────────────────────────────────────────────────────────

@cli.command()
def config() -> None:
    """Show current configuration."""
    cfg = ReinsConfig.load()
    click.echo(f"Storage: {cfg.storage}")
    click.echo(f"Storage path: {cfg.storage_path or Storage.DEFAULT_PATH}")
    click.echo(f"Retention: {cfg.retention_days} days")
    if cfg.budget.daily:
        click.echo(f"Daily budget: ${cfg.budget.daily}")
    if cfg.budget.agents:
        click.echo("Agent budgets:")
        for name, agent_cfg in cfg.budget.agents.items():
            click.echo(f"  {name}: per_run=${agent_cfg.per_run}, on_exceed={agent_cfg.on_exceed}")


@cli.command()
def version() -> None:
    """Show version information."""
    from reins import __version__
    click.echo(f"reins {__version__}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
