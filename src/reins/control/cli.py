"""Local operator commands. Credentials are read from private files."""

import csv
import json
import os
import secrets
from pathlib import Path

import click

from .client import Client
from .ledger import Ledger
from .server import ControlServer


@click.group()
@click.option("--url", default="http://127.0.0.1:8795")
@click.option("--token-file", default="~/.reins/control.token")
@click.pass_context
def control(ctx, url, token_file):
    """Shared runtime budget control (one host)."""
    ctx.obj = {"url": url, "token_file": str(Path(token_file).expanduser())}


@control.command()
@click.option("--database", default="~/.reins/control.sqlite")
@click.option("--port", type=click.IntRange(1, 65535), default=8795)
@click.option("--worker-token-file", default="~/.reins/worker.token")
@click.pass_context
def serve(ctx, database, port, worker_token_file):
    """Start the authoritative ledger and local operator dashboard."""
    path = Path(ctx.obj["token_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.stat().st_mode & 0o077:
            raise click.ClickException("Credential file must have permissions 0600")
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    ledger = Ledger(Path(database).expanduser())
    worker = Path(worker_token_file).expanduser()
    worker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(worker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if worker.stat().st_mode & 0o077:
            ledger.close()
            raise click.ClickException("Worker credential file must have permissions 0600")
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    server = ControlServer(
        ledger, path.read_text().strip(), port, worker_token=worker.read_text().strip()
    )
    click.echo(f"Control dashboard: http://127.0.0.1:{port}\nCredential file: {path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        ledger.close()


@control.command()
@click.argument("workflow_id", required=False)
@click.pass_context
def status(ctx, workflow_id):
    """Show recorded costs, reservations, tasks and forecasts."""
    click.echo(json.dumps(Client(**ctx.obj).post("status", {"workflow_id": workflow_id}), indent=2))


def transition(ctx, workflow_id, reason, action):
    click.echo(
        json.dumps(
            Client(**ctx.obj).post(
                "transition", {"workflow_id": workflow_id, "reason": reason, "action": action}
            )
        )
    )


@control.command()
@click.argument("workflow_id")
@click.option("--reason", required=True)
@click.pass_context
def pause(ctx, workflow_id, reason):
    """Block future admission; does not cancel in-flight provider calls."""
    transition(ctx, workflow_id, reason, "pause")


@control.command()
@click.argument("workflow_id")
@click.option("--reason", required=True)
@click.pass_context
def resume(ctx, workflow_id, reason):
    """Resume admission; existing reservations remain charged."""
    transition(ctx, workflow_id, reason, "resume")


@control.command()
@click.option("--csv", "csv_path", type=click.Path(exists=True), required=True)
@click.pass_context
def reconcile(ctx, csv_path):
    """Import explicit USD invoice lines; immutable, sequential revisions."""
    client = Client(**ctx.obj)
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            row["revision"] = int(row.get("revision") or 1)
            click.echo(json.dumps({"line_id": row.get("line_id"), **client.post("reconcile", row)}))


@control.command("apply")
@click.argument(
    "operation",
    type=click.Choice(
        [
            "pools",
            "tools",
            "approvals",
            "economics",
            "state/write",
            "state/read",
            "handoff",
            "workflows",
            "context/sample",
        ]
    ),
)
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def apply_operation(ctx, operation, input_file):
    """Submit an explicit JSON document to the local control API."""
    click.echo(
        json.dumps(
            Client(**ctx.obj).post(operation, json.loads(Path(input_file).read_text())), indent=2
        )
    )


@control.command("regression")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", required=True, type=click.Path())
def regression(input_file, output):
    """Evaluate a frozen trajectory suite; write HTML/JSON and fail on regression."""
    from .regression import evaluate_suite, write_report

    body = json.loads(Path(input_file).read_text())
    report = evaluate_suite(
        body["cases"], body["contract"], expected_case_ids=body["expected_case_ids"]
    )
    write_report(report, output)
    click.echo(
        f"{report['passing']}/{report['total']} cases passed; "
        f"report: {Path(output).with_suffix('.html')}"
    )
    if not report["passed"]:
        raise click.exceptions.Exit(1)
