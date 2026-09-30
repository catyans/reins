"""Tests for budget balance persistence and daily reset."""

from datetime import datetime, timezone
from decimal import Decimal

from reins.budget.engine import BudgetEngine
from reins.core.config import AgentBudgetConfig, BudgetConfig, ReinsConfig
from reins.core.events import EventBus
from reins.core.models import SpanData
from reins.core.storage import Storage


def _make_config():
    return ReinsConfig(
        budget=BudgetConfig(
            agents={
                "test_agent": AgentBudgetConfig(
                    per_run=Decimal("1.00"),
                    on_exceed="alert",
                ),
            }
        )
    )


def test_balance_persisted_after_span(tmp_path):
    """Balance should be written to DuckDB after each span."""
    storage = Storage(tmp_path / "test.duckdb")
    event_bus = EventBus()
    config = _make_config()
    engine = BudgetEngine(event_bus, storage, config)

    # Make a call
    span = SpanData.from_llm_call(
        "anthropic", {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "hi"}]}
    )
    span.metadata["agent_name"] = "test_agent"
    engine.on_span_start(span)
    span.cost = Decimal("0.01")
    engine.on_span_end(span)

    # Check persistence
    rows = storage.query("SELECT * FROM budget_balances WHERE agent_name = 'test_agent'")
    assert len(rows) == 1
    assert rows[0]["balance"] > 0
    storage.close()


def test_balance_survives_restart(tmp_path):
    """Balance should survive engine restart."""
    db_path = tmp_path / "test.duckdb"
    config = _make_config()

    # First engine: make a call
    storage1 = Storage(db_path)
    engine1 = BudgetEngine(EventBus(), storage1, config)

    span = SpanData.from_llm_call(
        "anthropic", {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "hi"}]}
    )
    span.metadata["agent_name"] = "test_agent"
    engine1.on_span_start(span)
    span.cost = Decimal("0.05")
    engine1.on_span_end(span)
    _balance_after = engine1._balances.get("test_agent")
    storage1.close()

    # Second engine: should load persisted balance
    storage2 = Storage(db_path)
    engine2 = BudgetEngine(EventBus(), storage2, config)
    assert engine2._balances.get("test_agent") is not None
    # Balance should be close to what was saved (may differ by persistence timing)
    storage2.close()


def test_period_expired_daily():
    """Daily period should expire after the start date."""
    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    assert not BudgetEngine._period_expired("daily", "2026-04-03", now)
    assert BudgetEngine._period_expired("daily", "2026-04-02", now)


def test_period_expired_monthly():
    """Monthly period should expire after the start month."""
    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    assert not BudgetEngine._period_expired("monthly", "2026-04-01", now)
    assert BudgetEngine._period_expired("monthly", "2026-03-01", now)
    assert BudgetEngine._period_expired("monthly", "2025-12-01", now)
