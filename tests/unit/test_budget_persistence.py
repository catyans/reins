from datetime import datetime, timezone
from decimal import Decimal

import pytest

from reins.budget.engine import BudgetEngine, BudgetExceededError
from reins.core.config import BudgetConfig
from reins.core.storage import Storage
from tests.unit.test_budget_engine import configuration, request, settle


def test_pending_reservation_survives_real_reopen(tmp_path, event_bus):
    path = tmp_path / "ledger.db"
    cfg = configuration(budget=BudgetConfig(daily=Decimal(".002")))
    s = Storage(path)
    e = BudgetEngine(event_bus, s, cfg)
    span = request()
    e.on_span_start(span)
    s.close()
    s = Storage(path)
    e = BudgetEngine(event_bus, s, cfg)
    try:
        with pytest.raises(BudgetExceededError):
            e.on_span_start(request("new"))
        e.reconcile(span.span_id, 0)
        e.on_span_start(request("new"))
    finally:
        s.close()


@pytest.mark.parametrize(
    "period,start,end",
    [
        ("daily", "2026-09-29T23:59:59", "2026-09-30T00:00:00"),
        ("monthly", "2026-09-30T23:59:59", "2026-10-01T00:00:00"),
        ("monthly", "2026-12-31T23:59:59", "2027-01-01T00:00:00"),
    ],
)
def test_rollover_without_process_restart(storage, event_bus, monkeypatch, period, start, end):
    e = BudgetEngine(
        event_bus, storage, configuration(budget=BudgetConfig(**{period: Decimal(".002")}))
    )
    monkeypatch.setattr(
        e, "_now", lambda: datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    )
    s = request("old")
    e.on_span_start(s)
    monkeypatch.setattr(e, "_now", lambda: datetime.fromisoformat(end).replace(tzinfo=timezone.utc))
    # Old settlement belongs to old reservation scopes, not today's budget.
    settle(e, s)
    e.on_span_start(request("new"))
    with pytest.raises(BudgetExceededError):
        e.on_span_start(request("another"))


def test_legacy_balance_is_not_imported(storage, event_bus):
    storage.query("CREATE TABLE budget_balances(agent_name VARCHAR,balance DOUBLE)")
    storage.query("INSERT INTO budget_balances VALUES ('agent',-100)")
    e = BudgetEngine(event_bus, storage, configuration())
    e.on_span_start(request())
    assert storage.query("SELECT balance FROM budget_balances")[0]["balance"] == -100


def test_single_writer(storage, tmp_db):
    with pytest.raises(RuntimeError):
        Storage(tmp_db)
