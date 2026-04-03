"""Tests for DuckDB storage."""

from decimal import Decimal

from reins.core.models import RunData, SpanData


def test_insert_and_query_span(storage):
    span = SpanData(
        run_id="run-1",
        provider="anthropic",
        model="claude-sonnet-4",
        name="anthropic.chat.create",
    )
    span.complete(tokens_in=100, tokens_out=50)

    # Need a run first
    run = RunData(run_id="run-1", agent_name="test_agent")
    storage.insert_run(run)
    storage.insert_span(span)

    rows = storage.query("SELECT * FROM spans WHERE run_id = 'run-1'")
    assert len(rows) == 1
    assert rows[0]["provider"] == "anthropic"
    assert rows[0]["tokens_in"] == 100
    assert rows[0]["tokens_out"] == 50


def test_insert_and_query_run(storage):
    run = RunData(run_id="run-2", agent_name="my_agent")
    storage.insert_run(run)

    rows = storage.query("SELECT * FROM runs WHERE run_id = 'run-2'")
    assert len(rows) == 1
    assert rows[0]["agent_name"] == "my_agent"
    assert rows[0]["status"] == "running"


def test_update_run(storage):
    run = RunData(run_id="run-3", agent_name="test")
    storage.insert_run(run)
    run.complete("completed")
    run.total_cost = Decimal("0.05")
    storage.update_run(run)

    rows = storage.query("SELECT * FROM runs WHERE run_id = 'run-3'")
    assert rows[0]["status"] == "completed"
    assert rows[0]["total_cost"] == 0.05


def test_budget_event(storage):
    storage.insert_budget_event(
        agent_name="test_agent",
        event_type="reserve",
        amount=0.01,
        balance_after=0.49,
        run_id="run-1",
        span_id="span-1",
    )

    rows = storage.query("SELECT * FROM budget_ledger WHERE agent_name = 'test_agent'")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "reserve"


def test_empty_query(storage):
    rows = storage.query("SELECT * FROM spans")
    assert rows == []
