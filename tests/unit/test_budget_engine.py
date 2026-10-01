"""Behavioral contract for v0.2 admission and durable accounting."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from reins.budget.engine import BudgetEngine, BudgetExceededError, CostBoundError
from reins.core.config import AgentBudgetConfig, BudgetConfig, ReinsConfig
from reins.core.models import SpanData
from reins.core.pricing import UnknownPriceError


def request(run="run", budget="0.01", agent="agent", model="expensive"):
    s = SpanData.from_llm_call("test", {"model": model, "max_tokens": 100})
    s.run_id = run
    s.estimated_input_tokens = 100
    s.metadata.update(
        agent_name=agent, budget_limit=budget, task_type="extract", input_bound_verified=True
    )
    return s


def configuration(mode="enforce", **kwargs):
    return ReinsConfig(
        mode=mode, prices={"test": {"expensive": ["10", "10"], "cheap": ["1", "1"]}}, **kwargs
    )


def settle(engine, span, cost="0.002"):
    span.cost = Decimal(cost)
    span.metadata["cost_status"] = "known"
    engine.on_span_end(span)


def test_independent_same_named_runs(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    for run in ["one", "two"]:
        s = request(run, "0.002")
        e.on_span_start(s)
        settle(e, s)
        with pytest.raises(BudgetExceededError):
            e.on_span_start(request(run, "0.002"))


def test_global_budget_shared_and_simultaneous_limits(storage, event_bus):
    cfg = configuration(
        budget=BudgetConfig(
            daily=Decimal(".003"),
            monthly=Decimal(".1"),
            agents={"a": AgentBudgetConfig(per_run=Decimal(".004"), daily=Decimal(".05"))},
        )
    )
    e = BudgetEngine(event_bus, storage, cfg)
    e.on_span_start(request("a1", agent="a"))
    with pytest.raises(BudgetExceededError):
        e.on_span_start(request("b1", agent="b"))


def test_monthly_only_and_zero_budget(storage, event_bus):
    e = BudgetEngine(
        event_bus, storage, configuration(budget=BudgetConfig(monthly=Decimal(".002")))
    )
    e.on_span_start(request("one"))
    with pytest.raises(BudgetExceededError):
        e.on_span_start(request("two"))
    with pytest.raises(BudgetExceededError):
        e.on_span_start(request("three", "0"))


def test_observe_never_changes_model(storage, event_bus):
    e = BudgetEngine(
        event_bus,
        storage,
        configuration("observe", task_models={"extract": ["test/expensive", "test/cheap"]}),
    )
    s = request(budget=".0003")
    s.metadata["on_exceed"] = "degrade"
    e.on_span_start(s)
    assert s.model == "expensive" and not s.degraded
    assert s.metadata["recommended_model"] == "cheap"
    assert storage.query("SELECT reserved FROM budget_requests")[0]["reserved"] == Decimal(".002")


def test_degrade_only_approved_affordable_candidate(storage, event_bus):
    e = BudgetEngine(
        event_bus, storage, configuration(task_models={"extract": ["test/expensive", "test/cheap"]})
    )
    s = request(budget=".0003")
    s.metadata["on_exceed"] = "degrade"
    e.on_span_start(s)
    assert s.model == "cheap" and s.degraded
    t = request("other", "0")
    t.metadata["on_exceed"] = "degrade"
    with pytest.raises(BudgetExceededError):
        e.on_span_start(t)


def test_no_implicit_degradation_chain(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request(budget=".0003")
    s.metadata["on_exceed"] = "degrade"
    with pytest.raises(BudgetExceededError):
        e.on_span_start(s)


def test_unknown_price_observe_and_strict(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    with pytest.raises(UnknownPriceError):
        e.on_span_start(request(model="unknown"))
    o = BudgetEngine(event_bus, storage, configuration("observe"))
    s = request(model="unknown")
    o.on_span_start(s)
    assert storage.query("SELECT reserved FROM budget_requests")[0]["reserved"] is None
    with pytest.raises(CostBoundError):
        e.on_span_start(request())
    o.reconcile(s.span_id, 0)
    e.on_span_start(request())


def test_verified_bounds_required(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request()
    s.metadata["input_bound_verified"] = False
    with pytest.raises(CostBoundError):
        e.on_span_start(s)


def test_pending_and_idempotent_reconciliation(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request(budget=".002")
    e.on_span_start(s)
    e.on_span_end(s)
    with pytest.raises(BudgetExceededError):
        e.on_span_start(request(budget=".002"))
    e.reconcile(s.span_id, ".001")
    e.reconcile(s.span_id, ".001")
    with pytest.raises(ValueError):
        e.reconcile(s.span_id, ".002")
    assert storage.query("SELECT actual FROM budget_requests")[0]["actual"] == Decimal(".001")


def test_settlement_once_and_no_reuse(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request()
    e.on_span_start(s)
    settle(e, s, ".001")
    settle(e, s, ".001")
    assert e._available(e._scopes(s)) == Decimal(".009")
    with pytest.raises(ValueError):
        e.on_span_start(s)


def test_100_concurrent_requests_share_one_cap(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())

    def work(_):
        try:
            e.on_span_start(request(budget=".01"))
            return True
        except BudgetExceededError:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = sum(pool.map(work, range(100)))
    assert accepted == 5
    assert storage.query("SELECT SUM(reserved) AS total FROM budget_requests")[0][
        "total"
    ] == Decimal(".01")


def test_nested_run_constrains_parent(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request("child")
    s.metadata["budget_ancestors"] = [("parent", ".002")]
    e.on_span_start(s)
    t = request("parent", ".002")
    with pytest.raises(BudgetExceededError):
        e.on_span_start(t)


def test_missing_run_rejected(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    with pytest.raises(ValueError):
        e.on_span_start(request(""))


def test_candidate_recount(storage, event_bus):
    e = BudgetEngine(
        event_bus, storage, configuration(task_models={"extract": ["test/expensive", "test/cheap"]})
    )
    s = request(budget=".0003")
    s.metadata["on_exceed"] = "degrade"
    s._bound_counter = lambda _: 10000
    with pytest.raises(BudgetExceededError):
        e.on_span_start(s)


def test_mispriced_or_invalid_bound_stops_future_calls(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request()
    e.on_span_start(s)
    settle(e, s, ".004")
    with pytest.raises(CostBoundError):
        e.on_span_start(request("another"))
    e.reconcile(s.span_id, ".004")
    e.on_span_start(request("another"))


def test_sql_like_agent_name_remains_data(storage, event_bus):
    e = BudgetEngine(event_bus, storage, configuration())
    s = request(agent="a'; DROP TABLE runs; --")
    e.on_span_start(s)
    assert (
        storage.query("SELECT agent FROM budget_requests")[0]["agent"] == s.metadata["agent_name"]
    )
    assert storage.query("SELECT count(*) AS n FROM runs")[0]["n"] == 0
