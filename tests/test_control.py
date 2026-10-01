"""Economic control safety invariants, including cross-process contention."""

import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

import pytest

from reins.control import Client, ControlDenied, bind_context, workflow
from reins.control.ledger import Ledger, amount
from reins.control.server import ControlServer


@pytest.fixture
def ledger(tmp_path):
    obj = Ledger(tmp_path / "control.sqlite")
    yield obj
    obj.close()


def start(ledger, wid="w", **kwargs):
    return ledger.workflow(
        {
            "workflow_id": wid,
            "customer_id": "customer",
            "task_type": "research",
            "budget": "1",
            "mode": "enforce",
            **kwargs,
        }
    )


def request(ledger, rid="r", task="w", cost="0.1", **kwargs):
    return ledger.reserve(
        {
            "request_id": rid,
            "task_id": task,
            "model": "google/test",
            "estimated_cost": cost,
            "max_cost": cost,
            **kwargs,
        }
    )


def settle(ledger, rid="r", cost="0.05", **kwargs):
    return ledger.settle({"request_id": rid, "actual_cost": cost, **kwargs})


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", True, "bad", "0.0000000001"])
def test_exact_currency(bad):
    with pytest.raises(ValueError):
        amount(bad)


def test_parent_budget_and_idempotency(ledger):
    start(ledger)
    ledger.branch({"parent_task_id": "w", "task_id": "a", "budget": "2"})
    assert ledger.branch({"parent_task_id": "w", "task_id": "a", "budget": "2"})
    ledger.branch({"parent_task_id": "w", "task_id": "b", "budget": "1"})
    assert request(ledger, task="a", cost="0.7")["decision"] == "continue"
    assert request(ledger, task="a", cost="0.7")["decision"] == "hold"
    with pytest.raises(ValueError):
        request(ledger, task="a", cost="0.6")
    assert request(ledger, "r2", "b", "0.4")["decision"] == "pause"
    settle(ledger, cost="0.2")
    assert request(ledger, "r2", "b", "0.4")["decision"] == "continue"
    status = ledger.status()["workflows"][0]
    assert Decimal(status["known_cost"]) + Decimal(status["reserved_cost"]) == Decimal(".6")


def test_reuse_only_validated_and_unknown_held(ledger):
    start(ledger)
    args = {"cache_key": "exact-input", "read_only": True}
    request(ledger, **args)
    assert request(ledger, "r2", **args)["decision"] == "hold"
    ledger.uncertain({"request_id": "r"})
    assert request(ledger, "r2", **args)["decision"] == "hold"
    settle(ledger, result={"ok": True}, validated=True)
    assert request(ledger, "r2", **args)["result"] == {"ok": True}
    assert len(ledger.status()["workflows"][0]["requests"]) == 1
    with pytest.raises(ValueError):
        request(ledger, "r3", cache_key="write")


def test_limits_observe_and_pause(ledger):
    start(ledger, max_branches=0, max_iterations=1, max_retries=0)
    assert ledger.branch({"parent_task_id": "w", "task_id": "a"})["reason"] == "branch_limit"
    assert request(ledger, retry=True)["reason"] == "retry_limit"
    request(ledger)
    assert request(ledger, "r2")["reason"] == "iteration_limit"
    start(ledger, "observe", mode="observe", budget="0")
    assert request(ledger, "o", "observe")["decision"] == "continue"
    assert ledger.status("observe")["workflows"][0]["events"][1]["kind"] == "would_pause"
    ledger.transition({"workflow_id": "w", "action": "pause", "reason": "operator"})
    assert request(ledger, "r3")["reason"] == "workflow_not_running"


def test_reconciliation_revisions_and_bound_excess(ledger):
    start(ledger)
    request(ledger)
    line = dict(
        provider="google",
        invoice_id="invoice",
        line_id="line",
        request_id="r",
        amount="0.2",
        reference="private.csv:2",
        revision=1,
        currency="USD",
    )
    assert ledger.reconcile(line)["matched"]
    assert ledger.reconcile(line)["duplicate"]
    assert ledger.status()["workflows"][0]["state"] == "paused"
    assert ledger.reconcile({**line, "revision": 2, "amount": "0.08"})["matched"]
    with pytest.raises(ValueError):
        ledger.reconcile({**line, "revision": 2, "amount": "0.09"})
    assert ledger.status()["workflows"][0]["bill_reconciled_cost"] == "0.08"
    assert not ledger.reconcile({**line, "line_id": "unknown", "request_id": "absent"})["matched"]
    assert ledger.status()["unmatched_bill_lines"] == 1


def test_crash_reservations_survive_restart(tmp_path):
    path = tmp_path / "ledger.sqlite"
    first = Ledger(path)
    start(first)
    request(first, cost="0.8")
    with pytest.raises(RuntimeError):
        Ledger(path)
    first.close()
    second = Ledger(path)
    try:
        assert request(second, cost="0.8")["decision"] == "hold"
        assert request(second, "next", cost="0.3")["decision"] == "pause"
    finally:
        second.close()


def test_forecast_has_minimum_history_and_customer_boundary(ledger):
    for i in range(30):
        wid = f"history-{i}"
        start(ledger, wid)
        ledger.progress({"task_id": wid, "stage": "extract", "completed": 1, "total": 2})
        request(ledger, wid, wid, "0.1")
        settle(ledger, wid, "0.1")
        ledger.transition({"workflow_id": wid, "action": "finish", "accepted": True})
    start(ledger)
    ledger.progress({"task_id": "w", "stage": "extract", "completed": 1, "total": 2})
    assert ledger.forecast("w")["remaining_p90"] == "0.1"
    assert ledger.forecast("w")["advisory_only"]
    start(ledger, "other", customer_id="other")
    ledger.progress({"task_id": "other", "stage": "extract", "completed": 1, "total": 2})
    assert ledger.forecast("other")["samples"] == 0


@pytest.fixture
def service(ledger):
    server = ControlServer(ledger, "x" * 48, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(f"http://127.0.0.1:{server.server_port}", token="x" * 48)
    yield client
    server.shutdown()
    server.server_close()
    thread.join()


def _worker_reserve(args):
    url, worker = args
    client = Client(url, token="x" * 48)
    return [
        client.post(
            "reserve",
            {
                "request_id": f"{worker}-{i}",
                "task_id": f"child-{worker}",
                "model": "google/test",
                "estimated_cost": "0.03",
                "max_cost": "0.03",
            },
        )["decision"]
        for i in range(25)
    ]


def test_four_processes_hundred_requests(service, ledger):
    start(ledger, max_iterations=200)
    for i in range(4):
        ledger.branch({"parent_task_id": "w", "task_id": f"child-{i}"})
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_worker_reserve, [(service.url, i) for i in range(4)]))
    assert sum(d == "continue" for batch in results for d in batch) == 33
    assert ledger.status()["workflows"][0]["reserved_cost"] == "0.99"


def test_sdk_explicit_calls_and_context(service):
    executions = []
    with workflow(
        client=service, customer_id="c", task_type="research", budget=".2", mode="enforce"
    ) as root:
        with root.task("child") as child:
            context = child.export_context()
        with bind_context(context, client=service) as child:
            for _ in range(2):
                result = child.call(
                    lambda: (executions.append(1) or {"value": 42}, ".05"),
                    model="google/test",
                    category="model",
                    max_cost=".1",
                    read_only=True,
                    reuse_inputs={"query": "frozen"},
                    validator=lambda r: r["value"] == 42,
                )
                assert result["value"] == 42
            with pytest.raises(ControlDenied):
                child.call(lambda: (None, ".3"), model="google/test", max_cost=".3")
        root.finish(accepted=True)
    assert len(executions) == 1
    record = service.post("status", {})["workflows"][0]
    assert record["cost_per_accepted_result"] == "0.05"


def test_uncertain_exception_is_not_free(service):
    def fail():
        raise TimeoutError("ambiguous provider timeout")

    with workflow(
        client=service, customer_id="c", task_type="research", budget=".1", mode="enforce"
    ) as root:
        with pytest.raises(TimeoutError):
            root.call(fail, model="google/test", max_cost=".1")
        with pytest.raises(ControlDenied):
            root.call(lambda: ({}, ".01"), model="google/test", max_cost=".01")
    assert service.post("status", {})["workflows"][0]["reserved_cost"] == "0.1"


def test_sdk_admission_downgrade_and_settlement(service, tmp_path):
    from types import SimpleNamespace

    from reins.core.config import ReinsConfig
    from reins.core.events import EventBus
    from reins.core.instrumentor import Instrumentor
    from reins.core.storage import Storage

    cfg = ReinsConfig(
        prices={"openai": {"large": ["10", "10"], "small": ["1", "1"]}},
        token_counter=lambda *_: 100,
    )
    storage = Storage(tmp_path / "worker.duckdb")
    inst = Instrumentor(EventBus(), storage, [], cfg)
    called = []

    def provider(client, **kwargs):
        called.append(kwargs["model"])
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10))

    try:
        with workflow(
            client=service,
            customer_id="c",
            task_type="research",
            budget=".0003",
            mode="enforce",
            approved_models=["openai/large", "openai/small"],
        ):
            inst._wrap_sync(
                provider, None, "openai", (), {"model": "large", "max_tokens": 100, "messages": []}
            )
        assert called == ["small"]
        row = service.post("status", {})["workflows"][0]
        assert row["known_cost"] == "0.00003"
        assert row["reserved_cost"] == "0"
    finally:
        storage.close()


def test_auth_and_origin(service):
    import urllib.error
    import urllib.request

    for headers in (
        {},
        {"Authorization": "Bearer " + "x" * 48, "Origin": "https://attacker.example"},
    ):
        req = urllib.request.Request(service.url + "/v1/status", data=b"{}", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        assert error.value.code == 403


def test_attribution_is_explicit():
    from reins.control.usage import token_attribution

    assert token_attribution(100, 20, system=30, tool_schema=10)["unattributed"] == 60
    with pytest.raises(ValueError):
        token_attribution(100, 20, system=110)


def test_service_outage_modes():
    from reins.control import ControlUnavailable

    class Offline:
        def post(self, *_):
            raise ControlUnavailable("offline")

    executed = []
    with pytest.raises(ControlUnavailable):
        with workflow(
            client=Offline(), customer_id="c", task_type="research", budget="1", mode="enforce"
        ):
            executed.append(True)
    assert not executed
    with workflow(
        client=Offline(), customer_id="c", task_type="research", budget="1", mode="observe"
    ) as task:
        task.call(lambda: (executed.append(True), ".1"), model="google/test", max_cost=".1")
    assert executed == [True]


def test_cli_status_and_dashboard(service, tmp_path):
    import urllib.request

    from click.testing import CliRunner

    from reins.cli.main import cli

    token = tmp_path / "credential"
    token.write_text("x" * 48)
    result = CliRunner().invoke(
        cli, ["control", "--url", service.url, "--token-file", str(token), "status"]
    )
    assert result.exit_code == 0
    assert '"workflows": []' in result.output
    with urllib.request.urlopen(service.url) as response:
        assert b"Every task. One shared budget." in response.read()
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_forecast_excludes_unresolved_progress(ledger):
    for i in range(30):
        wid = f"ambiguous-{i}"
        start(ledger, wid)
        request(ledger, wid, wid, "0.1")
        ledger.progress({"task_id": wid, "stage": "extract", "completed": 1, "total": 2})
        settle(ledger, wid, "0.02")
        ledger.transition({"workflow_id": wid, "action": "finish", "accepted": True})
    start(ledger)
    ledger.progress({"task_id": "w", "stage": "extract", "completed": 1, "total": 2})
    assert ledger.forecast("w")["samples"] == 0


def test_finished_workflow_cannot_dispatch_even_in_observe(ledger):
    start(ledger, mode="observe")
    ledger.transition({"workflow_id": "w", "action": "finish", "accepted": True})
    assert request(ledger)["decision"] == "pause"


def test_latest_invoice_revision_resolves_unmatched_line(ledger):
    start(ledger)
    line = dict(
        provider="google",
        invoice_id="i",
        line_id="l",
        request_id="r",
        amount=".01",
        reference="bill.csv:1",
        revision=1,
    )
    assert not ledger.reconcile(line)["matched"]
    request(ledger)
    assert ledger.reconcile({**line, "revision": 2})["matched"]
    assert ledger.status()["unmatched_bill_lines"] == 0
