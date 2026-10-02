"""Operator-only commands; no public endpoint can dispatch a paid request."""

import asyncio
import json
from pathlib import Path

import click

from reins import configure
from reins.core.decorators import shutdown
from reins.projects.runtime import AVAILABLE_POLICIES, POLICIES, Gemini, Runner, State, load_key


@click.group()
def projects():
    """Run and inspect durable public-project update jobs locally."""


def options(fn):
    for decorator in (
        click.option("--dataset", type=click.Path(exists=True), required=True),
        click.option("--output", type=click.Path(), required=True),
        click.option("--control-url", default="http://127.0.0.1:8795"),
        click.option("--control-token-file", type=click.Path(exists=True)),
        click.option("--control-task", help="Existing operator-provisioned task ID"),
        click.option("--key-file", type=click.Path(exists=True)),
        click.option("--public-output", type=click.Path(), default=None),
        click.option(
            "--split", multiple=True, type=click.Choice(["development", "validation", "test"])
        ),
        click.option("--policy", multiple=True, type=click.Choice(AVAILABLE_POLICIES)),
        click.option("--budget", type=click.FloatRange(min=0, min_open=True), default=30.0),
    ):
        fn = decorator(fn)
    return fn


def execute(
    dataset,
    output,
    key_file,
    public_output,
    split,
    policy,
    budget,
    control_url="http://127.0.0.1:8795",
    control_token_file=None,
    control_task=None,
):
    dataset = json.loads(Path(dataset).read_text())
    state = State(output)
    runner = None
    try:
        configure(storage_path=str(Path(output) / "traces.duckdb"))
        controlled = None
        if bool(control_token_file) != bool(control_task):
            raise click.UsageError("Supply both --control-token-file and --control-task")
        if control_task:
            from reins.control.client import Client, Workflow

            client = Client(control_url, token_file=control_token_file)
            controlled = Workflow(client, client.post("context", {"task_id": control_task}))
        runner = Runner(
            state,
            Gemini(state, load_key(key_file), limit=budget, control=controlled),
            public_output or Path(output) / "public-status.json",
        )
        asyncio.run(runner.run(dataset, splits=split or None, policies=policy or POLICIES))
    finally:
        if runner:
            runner.close()
        state.close()
        shutdown()


@projects.command("run")
@options
def run(**kwargs):
    """Run a finite experiment; successful jobs are idempotent."""
    execute(**kwargs)


@projects.command("resume")
@options
@click.option(
    "--acknowledge-circuit",
    default=None,
    help="Operator reason to resume NEW work only; old uncertain requests stay held.",
)
def resume(acknowledge_circuit, **kwargs):
    """Resume saved jobs; uncertain paid requests remain held for reconciliation."""
    state = State(kwargs["output"])
    try:
        if acknowledge_circuit:
            state.acknowledge_circuit(acknowledge_circuit)
        state.resume_settled_jobs()
    finally:
        state.close()
    execute(**kwargs)


@projects.command("status")
@click.option("--output", type=click.Path(exists=True), required=True)
def status(output):
    state = State(output, writer=False)
    try:
        rows = state.db.execute("SELECT state,count(*) count FROM jobs GROUP BY state").fetchall()
        click.echo(json.dumps([dict(row) for row in rows], indent=2))
    finally:
        state.close()


@projects.command("export")
@click.option("--output", type=click.Path(exists=True), required=True)
@click.option("--destination", type=click.Path(), required=True)
def export(output, destination):
    """Export the sanitized, read-only projection; never calls a model."""
    state = State(output, writer=False)
    try:
        state.export(destination)
    finally:
        state.close()
