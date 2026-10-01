"""Durable lifecycle observations; never controls execution or billing."""

from __future__ import annotations

import contextvars
import json
import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

from reins.core.context import get_current_run

current_step = contextvars.ContextVar("reins_step", default=None)
logger = logging.getLogger("reins.monitor")


def now():
    return datetime.now(timezone.utc).isoformat()


class Monitor:
    def __init__(self, storage, bus, config):
        self.storage, self.bus, self.config = storage, bus, config
        self.instance_id = str(uuid4())
        self.stopped = threading.Event()
        storage.query("INSERT INTO runtime_instances VALUES (?,?,false)", [self.instance_id, now()])
        self.listeners = {
            "core.run_start": self.run_start,
            "core.run_end": self.run_end,
            "core.span_start": self.span_start,
            "core.span_end": self.span_end,
            "core.span_admitted": self.span_admitted,
        }
        for event, handler in self.listeners.items():
            bus.on(event, handler)
        self.thread = threading.Thread(target=self._heartbeat, daemon=True, name="reins-heartbeat")
        self.thread.start()

    def _heartbeat(self):
        while not self.stopped.wait(5):
            try:
                self.storage.query(
                    "UPDATE runtime_instances SET heartbeat=? WHERE instance_id=?",
                    [now(), self.instance_id],
                )
            except Exception:
                logger.exception("Unable to persist Reins heartbeat")

    def event(self, run_id, kind, span_id=None, payload=None):
        if not run_id:
            return
        self.storage.query(
            "INSERT INTO run_events (run_id,span_id,event_type,timestamp,payload) "
            "VALUES (?,?,?,?,?)",
            [run_id, span_id, kind, now(), json.dumps(payload or {}, ensure_ascii=False)],
        )

    def run_start(self, run):
        run.metadata["instance_id"] = self.instance_id
        self.storage.update_run(run)
        self.event(run.run_id, "run_start", payload={"name": run.agent_name})

    def run_end(self, run):
        reason = run.metadata.get("failure_reason", "")
        self.event(run.run_id, "run_end", payload={"status": run.status, "error": reason})

    def span_start(self, span):
        self.event(
            span.run_id,
            "span_start",
            span.span_id,
            {
                "name": span.name,
                "kind": span.span_type,
                "model": span.model,
                "parent_span_id": span.parent_span_id,
            },
        )

    def span_admitted(self, span):
        self.event(
            span.run_id,
            "span_update",
            span.span_id,
            {
                "name": span.name,
                "model": span.model,
                "model_requested": span.model_requested,
                "decision": span.metadata.get("budget_decision"),
            },
        )

    def span_end(self, span):
        self.event(
            span.run_id,
            "span_end",
            span.span_id,
            {
                "name": span.name,
                "kind": span.span_type,
                "status": span.status,
                "model": span.model,
                "model_requested": span.model_requested,
                "error": (span.error_message or "")[:300],
                "decision": span.metadata.get("budget_decision"),
                "tokens_in": span.tokens_in,
                "tokens_out": span.tokens_out,
                "parent_span_id": span.parent_span_id,
            },
        )

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=6)
        self.storage.query(
            "UPDATE runtime_instances SET stopped=true, heartbeat=? WHERE instance_id=?",
            [now(), self.instance_id],
        )
        for event, handler in self.listeners.items():
            self.bus.off(event, handler)


class step:
    """Explicit business step: `with step('fetch', kind='tool'):`; async supported."""

    def __init__(self, name, kind="custom"):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("step name must be nonempty")
        if kind not in {"custom", "tool", "retrieval"}:
            raise ValueError("step kind must be custom, tool or retrieval")
        self.name, self.kind = name, kind
        self.used = False

    def __enter__(self):
        from reins.core.decorators import _get_runtime

        run = get_current_run()
        if run is None:
            raise ValueError("step requires an active @trace task")
        if self.used:
            raise RuntimeError("Create a new step context for each operation")
        self.used = True
        self.monitor = _get_runtime().monitor
        self.run_id, self.span_id = run.run_id, str(uuid4())
        self.payload = {"name": self.name, "kind": self.kind, "parent_span_id": current_step.get()}
        self.monitor.event(self.run_id, "step_start", self.span_id, self.payload)
        self.token = current_step.set(self.span_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.monitor.event(
                self.run_id,
                "step_end",
                self.span_id,
                {
                    **self.payload,
                    "status": "error" if exc else "ok",
                    "error": exc_type.__name__ if exc_type else "",
                },
            )
        finally:
            current_step.reset(self.token)
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        return self.__exit__(*args)
