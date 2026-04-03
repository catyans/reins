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
