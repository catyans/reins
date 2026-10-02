"""Safety and recovery tests for practical runtime controls."""

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from reins.control import Client, ControlDenied, workflow
from reins.control.governance import fingerprint
from reins.control.ledger import Ledger
from reins.control.regression import evaluate_suite, write_report
from reins.control.server import ControlServer
from tests.test_control import request, settle, start

pytest_plugins = ["tests.test_control"]


def test_pool_contention_and_priority(ledger):
    ledger.pool(
        dict(pool_id="p", customer_id="customer", team_id="t", budget="1", critical_reserve=".2")
    )
    for i in range(10):
        start(ledger, str(i), pool_id="p", team_id="t")

    def attempt(i):
        return request(ledger, str(i), str(i % 10), ".03")["decision"]

    with ThreadPoolExecutor(10) as workers:
        result = list(workers.map(attempt, range(100)))
    assert result.count("continue") == 26
    start(ledger, "critical", pool_id="p", team_id="t", priority="critical")
    assert request(ledger, "urgent", "critical", ".2")["decision"] == "continue"
    assert request(ledger, "overflow", "critical", ".03")["reason"] == "pool_budget"
    assert ledger.status()["pools"][0]["committed"] == "0.98"
    with pytest.raises(ValueError):
        start(ledger, "other", customer_id="different", team_id="t", pool_id="p")
    assert not ledger.status("other")["workflows"]


def test_repeat_normalization_and_legitimate_repeats(ledger):
    start(ledger, repeat_limit=3, repeat_window_seconds=60)
    key = fingerprint({"tool": "fetch", "args": {"b": 2, "a": 1}})
    assert key == fingerprint({"args": {"a": 1, "b": 2}, "tool": "fetch"})
    for i in range(3):
        assert request(ledger, str(i), operation_key=key)["decision"] == "continue"
        settle(ledger, str(i), "0")
    assert request(ledger, "4", operation_key=key)["reason"] == "repeated_operation"
    assert request(ledger, "5", operation_key=fingerprint({"a": 2}))["decision"] == "continue"
    ledger.db.execute("UPDATE requests SET created=?", (time.time() - 61,))
    assert request(ledger, "6", operation_key=key)["decision"] == "continue"


def test_consecutive_failures_reset_and_restart(tmp_path):
    path = tmp_path / "ledger.sqlite"
    store = Ledger(path)
    start(store, failure_limit=2)
    request(store, "first")
    settle(store, "first", "0", success=False)
    request(store, "good")
    settle(store, "good", "0", success=True)
    request(store, "next")
    settle(store, "next", "0", success=False)
    assert request(store, "last")["decision"] == "continue"
    store.uncertain({"request_id": "last"})
    store.close()
    store = Ledger(path)
    try:
        assert request(store, "blocked")["reason"] == "consecutive_failures"
        assert store.status()["workflows"][0]["reserved_cost"] == "0.1"
    finally:
        store.close()


def test_limits_and_wrapup(ledger):
    start(ledger, max_depth=1, max_tools=1, max_iterations=1, wrapup_reserve=".1")
    ledger.branch(dict(parent_task_id="w", task_id="child"))
    assert ledger.branch(dict(parent_task_id="child", task_id="deep"))["reason"] == "depth_limit"
    assert request(ledger, cost=".91")["reason"] == "shared_budget"
    assert request(ledger, cost=".9", category="tool")["decision"] == "continue"
    settle(ledger, cost=".9")
    ledger.transition(dict(workflow_id="w", action="wrapup", reason="Save partial work"))
    assert request(ledger, "no")["reason"] == "wrapup_only"
    assert ledger.branch(dict(parent_task_id="w", task_id="no"))["reason"] == "workflow_not_running"
    assert request(ledger, "wrap", cost=".1", phase="wrapup")["decision"] == "continue"
    settle(ledger, "wrap", ".1")
    assert request(ledger, "again", cost="0", phase="wrapup")["reason"] == "wrapup_already_admitted"
    ledger.transition(dict(workflow_id="w", action="finish", accepted=False))
    assert ledger.status()["workflows"][0]["cost_per_accepted_result"] is None


def test_deadline_and_tool_boundary(ledger):
    start(ledger, max_tools=1, deadline=time.time() + 100)
    request(ledger, category="tool")
    assert request(ledger, "next", category="tool")["reason"] == "tool_limit"
    assert request(ledger, "model", category="model")["decision"] == "continue"
    start(ledger, "late", deadline=time.time() - 1)
    assert request(ledger, "late", "late")["reason"] == "deadline"


def tool_setup(ledger):
    start(ledger, allowed_tools=["send"])
    ledger.register_tool(
        dict(
            name="send",
            fields={"target": "string", "text": "string"},
            read_only=False,
            allowed_values={"target": ["approved@example.test"]},
        )
    )
    args = {"target": "approved@example.test", "text": "hello"}
    ledger.approval(dict(approval_id="a", task_id="w", tool_name="send", arguments=args))
    return dict(category="tool", tool_name="send", tool_arguments=args, approval_id="a")


def test_approval_exact_single_use_and_recovery(tmp_path):
    path = tmp_path / "ledger.sqlite"
    store = Ledger(path)
    args = tool_setup(store)
    with pytest.raises(ValueError):
        request(
            store,
            tool_arguments={"target": "approved@example.test", "text": "changed"},
            **{k: v for k, v in args.items() if k != "tool_arguments"},
        )
    with pytest.raises(ValueError):
        request(store, **args, read_only=True, cache_key="write")
    assert request(store, **args)["decision"] == "continue"
    store.close()
    store = Ledger(path)
    try:
        assert request(store, **args)["decision"] == "hold"
        with pytest.raises(ValueError):
            request(store, "second", **args)
    finally:
        store.close()


def test_approval_expiry_policy_and_task_scope(ledger):
    args = tool_setup(ledger)
    ledger.branch(dict(parent_task_id="w", task_id="child"))
    with pytest.raises(ValueError):
        request(ledger, task="child", **args)
    ledger.db.execute("UPDATE approvals SET expires=0")
    with pytest.raises(ValueError):
        request(ledger, **args)
    ledger.db.execute("UPDATE approvals SET expires=?", (time.time() + 100,))
    ledger.register_tool(
        dict(
            name="send",
            fields={"target": "string", "text": "string"},
            read_only=False,
            approval_required=True,
        )
    )
    with pytest.raises(ValueError):
        request(ledger, **args)


def test_worker_cannot_change_policy_or_approve(ledger):
    server = ControlServer(ledger, "o" * 48, 0, worker_token="w" * 48)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    from reins.control import ControlUnavailable

    worker = Client(f"http://127.0.0.1:{server.server_port}", token="w" * 48)
    try:
        for route in ["tools", "approvals", "pools", "economics", "reconcile", "workflows"]:
            with pytest.raises(ControlUnavailable):
                worker.post(route, {})
        start(ledger)
        with pytest.raises(ControlUnavailable):
            worker.post("transition", dict(workflow_id="w", action="resume", reason="bypass"))
        assert worker.post("context", {"task_id": "w"})["customer_id"] == "customer"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_state_conflict_expiry_handoff_and_customer(ledger):
    start(ledger)
    ledger.branch(dict(parent_task_id="w", task_id="child"))
    body = dict(
        task_id="w", name="sources", expected_version=0, payload={"url": "public"}, source="fetch"
    )
    assert ledger.state_write(body)["version"] == 1
    with pytest.raises(ValueError):
        ledger.state_write(body)
    transfer = dict(
        handoff_id="h",
        task_id="w",
        receiver_task_id="child",
        state_name="sources",
        version=1,
        payload={"goal": "extract", "inputs": {}, "completed": ["fetch"]},
    )
    assert ledger.handoff(transfer)["state"]["version"] == 1
    assert ledger.handoff(transfer)["context"]["task_id"] == "child"
    start(ledger, "other", customer_id="another")
    with pytest.raises(ValueError):
        ledger.handoff({**transfer, "receiver_task_id": "other"})
    with pytest.raises(ValueError):
        ledger.handoff({**transfer, "payload": {"goal": "missing inputs"}})
    ledger.state_write({**body, "expected_version": 1, "payload": {"url": "new"}})
    with pytest.raises(ValueError):
        ledger.handoff(transfer)
    ledger.db.execute("UPDATE shared_state SET expires=0")
    with pytest.raises(ValueError):
        ledger.state_read(body)


def test_economics_missing_and_non_llm_costs(ledger):
    start(ledger)
    request(ledger)
    ledger.economic_entry(
        dict(
            entry_id="human",
            workflow_id="w",
            kind="human_review",
            hours=".5",
            hourly_rate="20",
            reference="review-log",
        )
    )
    ledger.economic_entry(
        dict(
            entry_id="infra",
            workflow_id="w",
            kind="infrastructure",
            amount="2",
            reference="allocation-log",
        )
    )
    ledger.economic_entry(
        dict(entry_id="revenue", workflow_id="w", kind="revenue", amount="20", reference="order")
    )
    assert ledger.status()["workflows"][0]["economics"]["margin"] is None
    ledger.economic_entry(
        dict(
            entry_id="complete",
            workflow_id="w",
            kind="coverage_complete",
            reference="All tool, review and infrastructure costs included",
        )
    )
    assert ledger.status()["workflows"][0]["economics"]["margin"] is None
    settle(ledger, cost=".05")
    assert Decimal(ledger.status()["workflows"][0]["economics"]["margin"]) == Decimal("7.95")


@pytest.mark.asyncio
async def test_async_cancel_holds_reservation(service):
    started = asyncio.Event()

    async def execute():
        started.set()
        await asyncio.sleep(20)

    with workflow(client=service, customer_id="c", task_type="t", budget=".1", mode="enforce") as w:
        task = asyncio.create_task(w.acall(execute, model="google/test", max_cost=".1"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ControlDenied):
            await w.acall(execute, model="google/test", max_cost=".1")
    assert service.post("status", {})["workflows"][0]["reserved_cost"] == "0.1"


@pytest.mark.asyncio
async def test_async_settlement_and_validation(service):
    async def execute():
        return {"ok": True}, ".03"

    with workflow(client=service, customer_id="c", task_type="t", budget="1", mode="enforce") as w:
        assert await w.acall(
            execute, model="google/test", max_cost=".1", validator=lambda x: x["ok"]
        )
        assert Decimal(w.budget_state()["remaining"]) == Decimal(".97")


def test_migration_backup_and_idempotence(tmp_path):
    path = tmp_path / "old.sqlite"
    store = Ledger(path)
    start(store)
    request(store)
    tables = [
        "pools",
        "memberships",
        "operation_meta",
        "tools",
        "approvals",
        "shared_state",
        "handoffs",
        "economics",
        "context_samples",
    ]
    for table in reversed(tables):
        store.db.execute(f"DROP TABLE {table}")
    store.db.execute("UPDATE schema_version SET version=1")
    store.close()
    for _ in range(2):
        store = Ledger(path)
        assert store.status()["workflows"][0]["reserved_cost"] == "0.1"
        assert store.db.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        store.close()
    with sqlite3.connect(str(path) + ".pre-v2.sqlite") as backup:
        assert backup.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert backup.execute("SELECT count(*) FROM requests").fetchone()[0] == 1


def test_regression_catches_silent_wrong_path_and_missing_cases(tmp_path):
    contract = dict(
        required_tools=["fetch", "verify"],
        ordered_tools=["fetch", "verify"],
        required_validations=["source"],
        max_cost=".1",
    )
    case = dict(
        case_id="a", accepted=True, cost=".01", trajectory=[{"kind": "tool", "tool": "fetch"}]
    )
    report = evaluate_suite([case], contract, expected_case_ids=["a"])
    assert not report["passed"]
    assert len(report["cases"][0]["failures"]) == 3
    with pytest.raises(ValueError):
        evaluate_suite([case], contract, expected_case_ids=["a", "b"])
    write_report(report, tmp_path / "report")
    assert "Required tool missing" in (tmp_path / "report.html").read_text()


def test_context_estimates_distinct_from_provider_total(ledger):
    from reins.control.usage import context_revision_diff, prepare_context

    start(ledger)
    prepared = prepare_context(
        user="hello",
        retrieval="abc" * 100,
        max_retrieval_chars=5,
        tools=[{"name": "fetch"}, {"name": "send"}],
        allowed_tools=["fetch"],
    )
    assert prepared["retrieval_truncated"]
    assert prepared["tools"] == [{"name": "fetch"}]
    ledger.context_sample(
        dict(
            task_id="w",
            sample_id="s",
            revision="v1",
            components=prepared["component_estimates"],
            provider_input_tokens=12,
        )
    )
    sample = ledger.status()["workflows"][0]["context_samples"][0]
    assert sample["provider_input_tokens"] == 12
    assert sample["components_are_estimates"]
    assert context_revision_diff({"system": 10}, {"system": 20})["system"] == 10


def test_native_google_usage_and_unknown_price():
    from reins.google_usage import (
        IncompleteGoogleUsage,
        estimate_google_text_cost,
        google_text_prices,
    )

    prices = google_text_prices(
        "models/gemini-2.5-flash-lite", {"gemini-2.5-flash-lite": [".1", ".4", ".01"]}
    )
    assert estimate_google_text_cost(
        dict(
            promptTokenCount=100,
            candidatesTokenCount=20,
            thoughtsTokenCount=10,
            cachedContentTokenCount=50,
        ),
        prices,
    ) == Decimal(".0000175")
    with pytest.raises(IncompleteGoogleUsage):
        estimate_google_text_cost(dict(promptTokenCount=100), prices)
    with pytest.raises(IncompleteGoogleUsage):
        google_text_prices("unknown", {})
    with pytest.raises(IncompleteGoogleUsage):
        estimate_google_text_cost(
            dict(
                prompt_tokens=1,
                completion_tokens=1,
                completion_tokens_details={"reasoning_tokens": 2},
            ),
            prices,
        )


@pytest.mark.asyncio
async def test_data_agent_central_admission_and_single_charge(service, tmp_path, monkeypatch):
    import io

    from reins.projects.runtime import Gemini, State

    evidence = {
        "choices": [{"message": {"content": '{"answer":42}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    executed = []

    def provider(*args, **kwargs):
        executed.append(True)
        return io.BytesIO(json.dumps(evidence).encode())

    import urllib.request

    # Keep control Client's local opener real; only replace the provider urlopen.
    monkeypatch.setattr(urllib.request, "urlopen", provider)
    state = State(tmp_path / "jobs")
    try:
        with workflow(
            client=service, customer_id="c", task_type="data", budget=".1", mode="enforce"
        ) as w:
            caller = Gemini(state, "test-credential", control=w)
            assert await caller.call("prompt", "lite", "job") == {"answer": 42}
            assert await caller.call("prompt", "lite", "job") == {"answer": 42}
            status = service.post("status", {})["workflows"][0]
            assert len(status["requests"]) == 1
            assert Decimal(status["known_cost"]) == Decimal(".000003")
            assert status["context_samples"][0]["provider_input_tokens"] == 10
        assert len(executed) == 1
        with workflow(
            client=service, customer_id="c", task_type="data", budget="0", mode="enforce"
        ) as w:
            caller = Gemini(state, "test-credential", control=w)
            with pytest.raises(ControlDenied):
                await caller.call("another prompt", "lite", "blocked")
            assert (
                state.db.execute("SELECT count(*) FROM calls WHERE job_id='blocked'").fetchone()[0]
                == 0
            )
        assert len(executed) == 1
    finally:
        state.close()


def test_migration_failure_rolls_back_and_releases_lock(tmp_path, monkeypatch):
    path = tmp_path / "broken.sqlite"
    store = Ledger(path)
    start(store)
    # Emulate an unexpected v1 table conflict partway through the transactional migration.
    for table in [
        "context_samples",
        "economics",
        "handoffs",
        "shared_state",
        "approvals",
        "tools",
        "operation_meta",
        "memberships",
        "pools",
    ]:
        store.db.execute(f"DROP TABLE {table}")
    store.db.execute("UPDATE schema_version SET version=1")
    store.db.execute("CREATE TABLE tools(legacy TEXT)")
    store.close()
    with pytest.raises(sqlite3.OperationalError):
        Ledger(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM workflows").fetchone()[0] == 1
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='pools'").fetchone()
        db.execute("DROP TABLE tools")
    recovered = Ledger(path)
    recovered.close()


def test_lost_settlement_ack_never_repeats_dispatch(service):
    from reins.control import ControlUnavailable

    class LostAck:
        def post(self, path, body):
            result = service.post(path, body)
            if path == "settle":
                raise ControlUnavailable("Lost reply after commit")
            return result

    with workflow(
        client=LostAck(), customer_id="c", task_type="t", budget=".1", mode="enforce"
    ) as w:
        assert w.call(lambda: ({"ok": True}, ".02"), model="google/test", max_cost=".1")
    assert service.post("status", {})["workflows"][0]["known_cost"] == "0.02"


def test_admission_ack_lost_keeps_hold_without_dispatch(service):
    from reins.control import ControlUnavailable

    class LostAck:
        def post(self, path, body):
            result = service.post(path, body)
            if path == "reserve":
                raise ControlUnavailable("Lost reply after commit")
            return result

    executed = []
    with workflow(
        client=LostAck(), customer_id="c", task_type="t", budget=".1", mode="enforce"
    ) as w:
        with pytest.raises(ControlUnavailable):
            w.call(lambda: (executed.append(True), "0"), model="google/test", max_cost=".1")
    assert not executed
    assert service.post("status", {})["workflows"][0]["reserved_cost"] == "0.1"


def test_recorded_tool_path_exports_into_regression(service, ledger):
    ledger.register_tool(dict(name="fetch", fields={"url": "string"}, read_only=True))
    with workflow(
        client=service,
        customer_id="c",
        task_type="research",
        budget="1",
        mode="enforce",
        allowed_tools=["fetch"],
    ) as w:
        w.call(
            lambda: ({"verified": True}, ".01"),
            model="tool/fetch",
            max_cost=".1",
            category="tool",
            tool_name="fetch",
            tool_arguments={"url": "public-source"},
            validator=lambda r: r["verified"],
            validation_name="source",
        )
        w.finish(accepted=True)
    case = service.post("regression/case", {"workflow_id": w.context["workflow_id"]})
    report = evaluate_suite(
        [case],
        {"required_tools": ["fetch"], "required_validations": ["source"], "max_cost": ".02"},
        expected_case_ids=[case["case_id"]],
    )
    assert report["passed"]


@pytest.mark.asyncio
async def test_data_agent_without_trace_and_restart_cache(tmp_path, monkeypatch):
    import io
    import urllib.request

    from reins.projects.runtime import Gemini, State

    response = {
        "choices": [{"message": {"content": '{"answer":42}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    called = []

    def provider(*args, **kwargs):
        called.append(True)
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(urllib.request, "urlopen", provider)
    for _ in range(2):
        state = State(tmp_path)
        assert await Gemini(state, "test-key").call("prompt", "lite", "job") == {"answer": 42}
        state.close()
    assert len(called) == 1


def test_regression_approval_order_cost_and_latency():
    contract = dict(
        required_tools=["fetch", "write"],
        ordered_tools=["fetch", "write"],
        approval_tools=["write"],
        max_tool_calls=2,
        max_cost="1",
        max_latency_ms=100,
    )
    case = dict(
        case_id="c",
        accepted=True,
        cost=None,
        latency_ms=101,
        trajectory=[
            {"kind": "tool", "tool": "write"},
            {"kind": "tool", "tool": "fetch"},
            {"kind": "tool", "tool": "fetch"},
        ],
    )
    report = evaluate_suite([case], contract, expected_case_ids=["c"])
    assert len(report["cases"][0]["failures"]) == 5


def test_pause_during_call_settles_without_new_dispatch(service):
    entered, proceed = threading.Event(), threading.Event()
    with workflow(client=service, customer_id="c", task_type="t", budget="1", mode="enforce") as w:

        def execute():
            entered.set()
            assert proceed.wait(3)
            return {}, ".1"

        with ThreadPoolExecutor(1) as executor:
            pending = executor.submit(w.call, execute, model="google/test", max_cost=".2")
            assert entered.wait(3)
            service.post(
                "transition",
                dict(workflow_id=w.context["workflow_id"], action="pause", reason="operator"),
            )
            proceed.set()
            assert pending.result() == {}
        with pytest.raises(ControlDenied):
            w.call(execute, model="google/test", max_cost=".2")
        status = service.post("status", {})["workflows"][0]
        assert status["known_cost"] == "0.1" and status["reserved_cost"] == "0"


def _pool_process(args):
    url, index = args
    client = Client(url, token="x" * 48)
    return [
        client.post(
            "reserve",
            dict(
                task_id=f"pool-{index}",
                request_id=f"{index}-{i}",
                model="google/test",
                estimated_cost=".03",
                max_cost=".03",
            ),
        )["decision"]
        for i in range(25)
    ]


def test_pool_is_atomic_across_processes_and_workflows(service, ledger):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    ledger.pool(
        dict(pool_id="p", customer_id="customer", team_id="t", budget="1", critical_reserve=".2")
    )
    for i in range(4):
        start(ledger, f"pool-{i}", team_id="t", pool_id="p")
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as workers:
        results = list(workers.map(_pool_process, [(service.url, i) for i in range(4)]))
    assert sum(r == "continue" for batch in results for r in batch) == 26
    assert ledger.status()["pools"][0]["committed"] == "0.78"


def test_wrapup_cannot_resume_through_pause(ledger):
    start(ledger)
    for action in ["wrapup", "pause"]:
        ledger.transition(dict(workflow_id="w", action=action, reason="test"))
    with pytest.raises(ValueError):
        ledger.transition(dict(workflow_id="w", action="resume", reason="test"))


def test_failure_circuit_uses_completion_order_not_admission_order(ledger):
    start(ledger, failure_limit=1)
    request(ledger, "slow")
    request(ledger, "fast")
    settle(ledger, "fast", "0", success=True)
    ledger.uncertain({"request_id": "slow"})
    assert request(ledger, "later")["reason"] == "consecutive_failures"
