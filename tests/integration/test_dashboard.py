import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import duckdb
import pytest

from reins import configure, record_outcome, record_retry, step, trace
from reins.core.decorators import _get_runtime, shutdown
from reins.core.models import RunData, SpanData
from reins.core.monitor import current_step
from reins.core.storage import _MIGRATIONS, Storage
from reins.dashboard.server import snapshot


@pytest.fixture
def runtime(tmp_path):
    configure(
        storage_path=tmp_path / "dashboard.db",
        dashboard=True,
        dashboard_port=0,
        prices={"openai": {"fake": ["1", "1"]}},
        token_counter=lambda *args: 100,
    )
    yield _get_runtime()
    shutdown()


def get(runtime, path):
    with urlopen(runtime.dashboard_server.url + path, timeout=5) as response:
        return json.load(response)


def test_empty_and_local_read_only(runtime):
    assert get(runtime, "/api/runs")["items"] == []
    assert get(runtime, "/api/compare") == []
    for path in ("/", "/app.js", "/style.css"):
        with urlopen(runtime.dashboard_server.url + path) as response:
            assert response.status == 200
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    for request, status in [
        (Request(runtime.dashboard_server.url + "/api/runs", method="POST"), 501),
        (Request(runtime.dashboard_server.url + "/", headers={"Host": "evil.example"}), 403),
        (Request(runtime.dashboard_server.url + "/api/runs/not-found"), 404),
        (Request(runtime.dashboard_server.url + "/api/runs?page=bad"), 400),
    ]:
        with pytest.raises(HTTPError) as exc:
            urlopen(request)
        assert exc.value.code == status


@pytest.mark.asyncio
async def test_live_parallel_steps_and_parent_cost(runtime):
    release = asyncio.Event()
    ready = asyncio.Event()
    entered = 0

    @trace(task_type="parallel")
    async def child(name):
        nonlocal entered
        async with step(name, kind="tool"):
            entered += 1
            if entered == 2:
                ready.set()
            await release.wait()
        record_outcome(success=True)

    @trace(task_type="parallel")
    async def parent():
        async with step("batch"):
            await asyncio.gather(child("fetch A"), child("fetch B"))
        record_outcome(success=False)

    task = asyncio.create_task(parent())
    await ready.wait()
    data = get(runtime, "/api/runs")["items"]
    root = next(r for r in data if not r["parent_run_id"])
    assert root["state"] == "running"
    assert {s["name"] for s in root["active_steps"]} == {"batch", "fetch A", "fetch B"}
    detail = get(runtime, "/api/runs/" + root["run_id"])
    assert len(detail["children"]) == 2
    assert all(s["parent_span_id"] for s in root["active_steps"] if s["name"] != "batch")
    release.set()
    await task
    assert current_step.get() is None
    ended = get(runtime, "/api/runs/" + root["run_id"])
    assert ended["state"] == "completed" and ended["success"] is False
    assert ended["active_steps"] == []


@pytest.mark.asyncio
async def test_running_stream_persists_start_and_holds_budget(runtime):
    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def close(self):
            pass

    async def provider(*args, **kwargs):
        return Stream()

    @trace(budget=".01", mode="enforce")
    async def task():
        async with step("extract"):
            stream = await runtime.instrumentor._wrap_async(
                provider, None, "openai", (), {"model": "fake", "max_tokens": 100, "stream": True}
            )
            data = snapshot(runtime)[0]
            assert len(data["active_steps"]) == 2
            assert data["pending_requests"] == 1 and data["reserved_cost"] > 0
            await stream.aclose()
        record_outcome(success=True)

    await task()
    data = snapshot(runtime)[0]
    assert data["active_steps"] == []
    assert data["pending_requests"] == 1
    assert get(runtime, "/api/compare")[0]["cost_per_success"] is None


@pytest.mark.asyncio
async def test_budget_rejection_and_retry_visible(runtime):
    from reins.budget.engine import BudgetExceededError

    called = False

    async def provider(*args, **kwargs):
        nonlocal called
        called = True

    @trace(budget=0, mode="enforce")
    async def task():
        record_retry()
        async with step("extract"):
            await runtime.instrumentor._wrap_async(
                provider, None, "openai", (), {"model": "fake", "max_tokens": 100}
            )

    with pytest.raises(BudgetExceededError):
        await task()
    row = snapshot(runtime)[0]
    assert not called and row["state"] == "failed"
    assert row["pending_requests"] == 0 and row["known_cost"] == 0
    assert any(e["event_type"] == "retry" for e in row["events"])
    assert any(e["payload"].get("decision") == "rejected" for e in row["events"])
    assert current_step.get() is None


def test_inactivity_is_not_failure_and_missing_heartbeat_is_unknown(runtime):
    run = RunData(agent_name="quiet")
    runtime.storage.insert_run(run)
    runtime.monitor.run_start(run)
    old = (datetime.now(timezone.utc) - timedelta(seconds=80)).isoformat()
    runtime.storage.query("UPDATE run_events SET timestamp=?", [old])
    row = snapshot(runtime)[0]
    assert row["state"] == "running" and row["inactive"]
    runtime.storage.query("UPDATE runtime_instances SET heartbeat=?", [old])
    row = snapshot(runtime)[0]
    assert row["state"] == "unknown" and not row["inactive"]
    assert runtime.storage.query("SELECT status FROM runs")[0]["status"] == "running"


def test_restart_does_not_revive_old_run_or_release_reservation(tmp_path):
    db = tmp_path / "restart.db"
    configure(storage_path=db)
    runtime = _get_runtime()
    run = RunData()
    runtime.storage.insert_run(run)
    runtime.monitor.run_start(run)
    span = SpanData.from_llm_call("openai", {"model": "unknown"})
    span.run_id = run.run_id
    next(m for m in runtime.modules if m.name == "budget")._engine.on_span_start(span)
    shutdown()
    configure(storage_path=db)
    try:
        row = snapshot(_get_runtime())[0]
        assert row["state"] == "unknown" and row["pending_requests"] == 1
        assert row["unknown_reservations"] == 1
    finally:
        shutdown()


def test_migrate_v2_preserves_records(tmp_path):
    path = tmp_path / "old.db"
    con = duckdb.connect(str(path))
    con.execute(_MIGRATIONS[1])
    con.execute(_MIGRATIONS[2])
    con.execute(
        "INSERT INTO runs (run_id,agent_name,status,started_at) VALUES ('old','old','running',?)",
        [datetime.now(timezone.utc).isoformat()],
    )
    con.close()
    storage = Storage(path)
    try:
        assert storage.query("SELECT version FROM schema_version")[0]["version"] == 3
        assert storage.query("SELECT run_id FROM runs")[0]["run_id"] == "old"
        assert storage.query("SELECT * FROM run_events") == []
    finally:
        storage.close()


def test_pagination_filters_and_private_metadata(runtime):
    for i in range(53):
        r = RunData(agent_name="sample", metadata={"secret": "never-send", "task_type": "extract"})
        runtime.storage.insert_run(r)
        runtime.monitor.run_start(r)
        r.complete()
        runtime.storage.update_run(r)
    first = get(runtime, "/api/runs?agent=sample&task_type=extract")
    assert first["total"] == 53 and len(first["items"]) == 50
    assert len(get(runtime, "/api/runs?page=2")["items"]) == 3
    assert get(runtime, "/api/runs?state=failed")["items"] == []
    assert "never-send" not in json.dumps(first)


@pytest.mark.asyncio
async def test_cancelled_step_and_context_restore(runtime):
    @trace
    async def task():
        async with step("cancelled tool"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await task()
    row = snapshot(runtime)[0]
    assert row["state"] == "cancelled"
    assert row["active_steps"] == []
    assert current_step.get() is None
    assert row["events"][-2]["payload"]["error"] == "CancelledError"


def test_sync_step_validation(runtime):
    with pytest.raises(ValueError):
        with step("outside"):
            pass

    @trace
    def task():
        with step("outer"):
            outer = current_step.get()
            with step("inner"):
                assert current_step.get() != outer
            assert current_step.get() == outer
        assert current_step.get() is None

    task()
    assert snapshot(runtime)[0]["state"] == "completed"


def test_port_collision_cleans_up_runtime(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    db = tmp_path / "port.db"
    try:
        with pytest.raises(RuntimeError, match="Cannot start Reins dashboard"):
            configure(storage_path=db, dashboard=True, dashboard_port=sock.getsockname()[1])
        configure(storage_path=db)
        assert _get_runtime().dashboard_server is None
    finally:
        sock.close()
        shutdown()


def test_close_releases_http_port(runtime):
    port = runtime.dashboard_server.port
    runtime.dashboard_server.close()
    runtime.dashboard_server = None
    sock = socket.socket()
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_nested_costs_once_and_admission_visible(runtime):
    async def provider(*args, **kwargs):
        row = snapshot(runtime)[0]
        assert any(e["event_type"] == "span_update" for e in row["events"])
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100))

    @trace
    async def child():
        async with step("extract"):
            await runtime.instrumentor._wrap_async(
                provider, None, "openai", (), {"model": "fake", "max_tokens": 100}
            )
        record_outcome(success=True)

    @trace
    async def parent():
        await child()
        record_outcome(success=True)

    await parent()
    rows = snapshot(runtime)
    assert len(rows) == 2 and all(r["known_cost"] == 0.0002 for r in rows)
    assert get(runtime, "/api/compare")[0]["known_total_cost"] == 0.0002
    end = next(e for e in rows[0]["events"] if e["event_type"] == "span_end")
    assert end["payload"]["confirmed_cost"] == 0.0002
    assert end["payload"]["elapsed_seconds"] >= 0
