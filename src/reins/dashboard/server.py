"""Loopback HTTP view sharing the runtime's single DuckDB connection."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from reins.core.outcomes import compare_tasks

logger = logging.getLogger("reins.dashboard")


def age(value):
    return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())


def snapshot(runtime):
    storage = runtime.storage
    with storage._lock:
        runs = storage.query("SELECT * FROM runs ORDER BY started_at DESC")
        instances = {r["instance_id"]: r for r in storage.query("SELECT * FROM runtime_instances")}
        events = storage.query("SELECT * FROM run_events ORDER BY seq")
        requests = storage.query("SELECT * FROM budget_requests")
        external = storage.query("SELECT * FROM external_costs")
        outcomes = {r["run_id"]: r for r in storage.query("SELECT * FROM outcomes")}
    by_id = {r["run_id"]: r for r in runs}
    metas = {i: json.loads(r["metadata"] or "{}") for i, r in by_id.items()}
    starts = {}
    charges_by_span = {r["span_id"]: r for r in requests}
    for event in events:
        event["payload"] = json.loads(event["payload"])
        sid = event["span_id"]
        if event["event_type"] in {"span_start", "step_start"}:
            starts[sid] = event["timestamp"]
        if sid in starts:
            event["payload"]["elapsed_seconds"] = max(
                0,
                (
                    datetime.fromisoformat(event["timestamp"]) - datetime.fromisoformat(starts[sid])
                ).total_seconds(),
            )
        if sid in charges_by_span:
            charge = charges_by_span[sid]
            event["payload"]["confirmed_cost"] = (
                float(charge["actual"]) if charge["actual"] is not None else None
            )
            event["payload"]["reserved_cost"] = (
                float(charge["reserved"])
                if charge["actual"] is None and charge["reserved"] is not None
                else 0
            )
            event["payload"]["cost_pending"] = charge["actual"] is None
    result = []
    for run in runs:
        rid, meta = run["run_id"], metas[run["run_id"]]
        # Root rows roll up their descendants once; child detail shows its own records.
        ids = {rid}
        if not meta.get("parent_run_id"):
            ids.update(i for i, m in metas.items() if m.get("root_run_id") == rid)
        relevant = [e for e in events if e["run_id"] in ids]
        active = {}
        for event in relevant:
            if event["event_type"] in {"span_start", "step_start"}:
                active[event["span_id"]] = event
            elif event["event_type"] == "span_update" and event["span_id"] in active:
                original = active[event["span_id"]]
                active[event["span_id"]] = {
                    **original,
                    "payload": {**original["payload"], **event["payload"]},
                }
            elif event["event_type"] in {"span_end", "step_end"}:
                active.pop(event["span_id"], None)
        last = relevant[-1]["timestamp"] if relevant else run["started_at"]
        instance = instances.get(meta.get("instance_id"))
        fresh = bool(instance and not instance["stopped"] and age(instance["heartbeat"]) <= 15)
        state = run["status"]
        if state == "running" and not fresh:
            state = "unknown"
        if meta.get("failure_reason") in {"CancelledError", "KeyboardInterrupt"}:
            state = "cancelled"
        charges = [r for r in requests if r["run_id"] in ids]
        known = sum(float(r["actual"]) for r in charges if r["actual"] is not None)
        known += sum(float(r["amount"]) for r in external if r["run_id"] in ids)
        pending = [r for r in charges if r["actual"] is None]
        outcome = outcomes.get(rid)
        # Do not expose arbitrary run metadata or request payloads to the UI.
        result.append(
            {
                "run_id": rid,
                "agent": run["agent_name"],
                "task_type": meta.get("task_type", "default"),
                "policy": meta.get("policy_version", "baseline"),
                "mode": meta.get("mode", "observe"),
                "parent_run_id": meta.get("parent_run_id"),
                "state": state,
                "started_at": run["started_at"],
                "ended_at": run["ended_at"],
                "duration_seconds": (
                    datetime.fromisoformat(run["ended_at"])
                    - datetime.fromisoformat(run["started_at"])
                ).total_seconds()
                if run["ended_at"]
                else age(run["started_at"]),
                "last_activity": last,
                "inactive": state == "running" and age(last) > runtime.config.inactivity_seconds,
                "active_steps": [
                    {"id": e["span_id"], "run_id": e["run_id"], **e["payload"]}
                    for e in active.values()
                ]
                if state in {"running", "unknown"}
                else [],
                "known_cost": known,
                "pending_requests": len(pending),
                "reserved_cost": sum(
                    float(r["reserved"]) for r in pending if r["reserved"] is not None
                ),
                "unknown_reservations": sum(r["reserved"] is None for r in pending),
                "success": outcome["success"] if outcome else None,
                "score": outcome["score"] if outcome else None,
                "failure_reason": meta.get("failure_reason"),
                "simulated": meta.get("task_type", "").startswith("simulated_"),
                "events": relevant,
            }
        )
    return result


class DashboardServer:
    def __init__(self, runtime):
        self.runtime = runtime
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                # No remote binding, cross-origin access or arbitrary file routes.
                if self.headers.get("Host") not in {
                    f"127.0.0.1:{owner.port}",
                    f"localhost:{owner.port}",
                }:
                    self.send_error(403)
                    return
                try:
                    path = urlparse(self.path)
                    query = parse_qs(path.query)
                    if path.path in {"/", "/app.js", "/style.css"}:
                        name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[
                            path.path
                        ]
                        body = Path(__file__).with_name(name).read_bytes()
                        mime = {
                            "index.html": "text/html",
                            "app.js": "text/javascript",
                            "style.css": "text/css",
                        }[name]
                    elif path.path == "/api/runs":
                        rows = snapshot(runtime)
                        total_all = len(rows)
                        for field in ("agent", "state", "task_type"):
                            if query.get(field, [""])[0]:
                                rows = [r for r in rows if r[field] == query[field][0]]
                        rows.sort(
                            key=lambda r: (
                                r["state"] != "running",
                                -datetime.fromisoformat(r["started_at"]).timestamp(),
                            )
                        )
                        page = max(1, int(query.get("page", [1])[0]))
                        data = {
                            "total": len(rows),
                            "total_all": total_all,
                            "page": page,
                            "page_size": 50,
                            "items": [
                                {k: v for k, v in r.items() if k != "events"}
                                for r in rows[(page - 1) * 50 : page * 50]
                            ],
                        }
                        body, mime = json.dumps(data).encode(), "application/json"
                    elif path.path.startswith("/api/runs/"):
                        rows = snapshot(runtime)
                        rid = path.path.rsplit("/", 1)[1]
                        data = next((r for r in rows if r["run_id"] == rid), None)
                        if data is None:
                            self.send_error(404)
                            return
                        data["children"] = [
                            {"run_id": r["run_id"], "agent": r["agent"], "state": r["state"]}
                            for r in rows
                            if r["parent_run_id"] == rid
                        ]
                        body, mime = json.dumps(data).encode(), "application/json"
                    elif path.path == "/api/experiments":
                        from reins.optimization import list_experiments

                        data = list_experiments(runtime.storage)
                        body, mime = json.dumps(data).encode(), "application/json"
                    elif path.path == "/api/optimization":
                        from reins.optimization import recommend

                        data = recommend(runtime.storage, query.get("experiment", [""])[0])
                        body, mime = json.dumps(data).encode(), "application/json"
                    elif path.path == "/api/compare":
                        with runtime.storage._lock:
                            data = compare_tasks(runtime.storage)
                        body, mime = json.dumps(data).encode(), "application/json"
                    else:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", mime + "; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'self'; style-src 'self'; script-src 'self'; "
                        "connect-src 'self'; frame-ancestors 'none'",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ValueError, TypeError):
                    self.send_error(400)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    logger.exception("Dashboard read failed")
                    self.send_error(500)

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", runtime.config.dashboard_port), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot start Reins dashboard on port {runtime.config.dashboard_port}: {exc}"
            ) from exc
        self.port = self.httpd.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True, name="reins-dashboard"
        )
        self.thread.start()
        logger.warning("Reins dashboard: %s", self.url)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
