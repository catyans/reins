"""Single-host authoritative ledger. Amounts are integer nano-USD, never floats."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import sqlite3
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCALE = 1_000_000_000


def amount(value):
    if isinstance(value, bool):
        raise ValueError("Invalid USD amount")
    try:
        value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid USD amount") from exc
    if not value.is_finite():
        raise ValueError("USD must be finite")
    scaled = value * SCALE
    if (
        not value.is_finite()
        or value < 0
        or value > 100_000_000
        or scaled != scaled.to_integral_value()
    ):
        raise ValueError("USD requires a nonnegative amount with at most nine decimals")
    return int(scaled)


def dollars(value):
    return str(Decimal(value) / SCALE)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"Invalid {name}")
    return value


class Ledger:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lockfile = open(str(path) + ".lock", "a+")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            raise RuntimeError("A control service already owns this ledger") from None
        self.lock = threading.RLock()
        import os

        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS workflows(
          id TEXT PRIMARY KEY, customer TEXT NOT NULL, task_type TEXT NOT NULL,
          policy TEXT NOT NULL, mode TEXT NOT NULL, budget INTEGER NOT NULL,
          state TEXT NOT NULL, config TEXT NOT NULL, accepted INTEGER,
          created REAL NOT NULL, finished REAL);
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, workflow TEXT REFERENCES workflows(id), parent TEXT
          REFERENCES tasks(id),
          budget INTEGER NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(
          id TEXT PRIMARY KEY, workflow TEXT REFERENCES workflows(id), task TEXT
          REFERENCES tasks(id),
          fingerprint TEXT NOT NULL, model TEXT NOT NULL, category TEXT NOT NULL,
          estimated INTEGER NOT NULL, reserved INTEGER NOT NULL, actual INTEGER,
          state TEXT NOT NULL, cache_key TEXT, result TEXT, validated INTEGER NOT NULL DEFAULT 0,
          usage TEXT, created REAL NOT NULL, settled REAL);
        CREATE TABLE IF NOT EXISTS allocations(
          request TEXT REFERENCES requests(id), task TEXT REFERENCES tasks(id), PRIMARY
          KEY(request,task));
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, workflow TEXT, task TEXT, kind TEXT, payload TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS progress(
          id INTEGER PRIMARY KEY, workflow TEXT REFERENCES workflows(id), task TEXT
          REFERENCES tasks(id),
          stage TEXT, done INTEGER, total INTEGER, spent INTEGER, created REAL);
        CREATE TABLE IF NOT EXISTS bills(
          provider TEXT, invoice_id TEXT, line_id TEXT, request_id TEXT,
          amount INTEGER, revision INTEGER, reference TEXT, body TEXT, matched INTEGER,
          PRIMARY KEY(provider,invoice_id,line_id,revision));
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        """)

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def event(self, workflow, task, kind, **payload):
        self.db.execute(
            "INSERT INTO events(workflow,task,kind,payload,created) VALUES(?,?,?,?,?)",
            (workflow, task, kind, canonical(payload), time.time()),
        )

    def workflow(self, body):
        wid = identity(body["workflow_id"], "workflow_id")
        config = {
            "max_branches": body.get("max_branches", 16),
            "max_iterations": body.get("max_iterations", 100),
            "max_retries": body.get("max_retries", 3),
            "max_cost_per_accepted_result": body.get("max_cost_per_accepted_result"),
            "approved_models": body.get("approved_models", []),
        }
        for key in ("max_branches", "max_iterations", "max_retries"):
            if type(config[key]) is not int or config[key] < 0 or config[key] > 100000:
                raise ValueError("Limits must be bounded nonnegative integers")
        if config["max_cost_per_accepted_result"] is not None:
            amount(config["max_cost_per_accepted_result"])
        if not isinstance(config["approved_models"], list) or any(
            not isinstance(x, str) or "/" not in x for x in config["approved_models"]
        ):
            raise ValueError("approved_models must contain provider/model identifiers")
        values = (
            identity(body["customer_id"], "customer_id"),
            identity(body["task_type"], "task_type"),
            identity(body.get("policy_version", "baseline"), "policy_version"),
            body.get("mode", "observe"),
            amount(body["budget"]),
            canonical(config),
        )
        if values[3] not in ("observe", "enforce"):
            raise ValueError("mode must be observe or enforce")
        with self.transaction():
            old = self.db.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone()
            if old:
                if (
                    tuple(
                        old[k]
                        for k in ("customer", "task_type", "policy", "mode", "budget", "config")
                    )
                    != values
                ):
                    raise ValueError("Workflow identity cannot be rebound")
            else:
                self.db.execute(
                    "INSERT INTO workflows VALUES(?,?,?,?,?,?,?, ?,NULL,?,NULL)",
                    (wid, *values[:5], "running", values[5], time.time()),
                )
                self.db.execute(
                    "INSERT INTO tasks VALUES(?,?,NULL,?,?)", (wid, wid, values[4], time.time())
                )
                self.event(wid, wid, "workflow_started")
        return self.context(wid)

    def context(self, task):
        with self.lock:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
            if not row:
                raise ValueError("Unknown task")
            w = self.db.execute("SELECT * FROM workflows WHERE id=?", (row["workflow"],)).fetchone()
            return {
                "workflow_id": w["id"],
                "task_id": task,
                "parent_task_id": row["parent"],
                "customer_id": w["customer"],
                "task_type": w["task_type"],
                "mode": w["mode"],
                "policy_version": w["policy"],
                "approved_models": json.loads(w["config"])["approved_models"],
            }

    def _task(self, task):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
        if not row:
            raise ValueError("Unknown task")
        return row, self.db.execute(
            "SELECT * FROM workflows WHERE id=?", (row["workflow"],)
        ).fetchone()

    def branch(self, body):
        tid = identity(body["task_id"], "task_id")
        with self.transaction():
            parent, w = self._task(body["parent_task_id"])
            budget = amount(body["budget"]) if body.get("budget") is not None else parent["budget"]
            budget = min(budget, parent["budget"])
            old = self.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if old:
                if (old["parent"], old["budget"]) != (parent["id"], budget):
                    raise ValueError("Task identity cannot be rebound")
            else:
                cfg = json.loads(w["config"])
                count = (
                    self.db.execute(
                        "SELECT count(*) FROM tasks WHERE workflow=?", (w["id"],)
                    ).fetchone()[0]
                    - 1
                )
                reason = (
                    "workflow_not_running"
                    if w["state"] != "running"
                    else ("branch_limit" if count >= cfg["max_branches"] else None)
                )
                if reason and (w["mode"] == "enforce" or reason == "workflow_not_running"):
                    return {"decision": "pause", "reason": reason}
                if reason:
                    self.event(w["id"], tid, "would_pause", reason=reason)
                self.db.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?)",
                    (tid, w["id"], parent["id"], min(budget, parent["budget"]), time.time()),
                )
                self.event(w["id"], tid, "branch_created", parent=parent["id"])
        return {"decision": "continue", **self.context(tid)}

    def _charge(self, workflow):
        return self.db.execute(
            "SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM requests WHERE workflow=?",
            (workflow,),
        ).fetchone()[0]

    def reserve(self, body):
        rid = identity(body["request_id"], "request_id")
        estimate = amount(body["estimated_cost"])
        reserve = amount(body.get("max_cost", body["estimated_cost"]))
        if estimate > reserve:
            raise ValueError("Estimate exceeds declared bound")
        model = identity(body["model"], "model")
        category = body.get("category", "model")
        if category not in ("model", "tool", "evaluation"):
            raise ValueError("Unknown cost category")
        key = body.get("cache_key")
        if key is not None:
            identity(key, "cache_key")
            if body.get("read_only") is not True:
                raise ValueError("Reuse is restricted to declared read-only operations")
        retry = body.get("retry", False)
        if type(retry) is not bool:
            raise ValueError("retry must be boolean")
        fingerprint = hashlib.sha256(canonical(body).encode()).hexdigest()
        with self.transaction():
            t, w = self._task(body["task_id"])
            old = self.db.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Request ID reused with different arguments")
                # A lost reservation acknowledgement must never dispatch a second paid call.
                return {"decision": "hold", "reason": "request_already_reserved", "request_id": rid}
            cfg = json.loads(w["config"])
            if (
                cfg["approved_models"]
                and category == "model"
                and model not in cfg["approved_models"]
            ):
                raise ValueError("Model is not in the workflow approved-model set")
            reason = None
            if w["state"] != "running":
                reason = "workflow_not_running"
            if not reason and key:
                cached = self.db.execute(
                    "SELECT * FROM requests WHERE workflow=? AND cache_key=? ORDER BY created "
                    "DESC LIMIT 1",
                    (w["id"], key),
                ).fetchone()
                if cached and cached["state"] in ("settled", "reconciled") and cached["validated"]:
                    self.event(w["id"], t["id"], "reused", original_request=cached["id"])
                    return {
                        "decision": "reuse",
                        "result": json.loads(cached["result"]),
                        "original_request": cached["id"],
                    }
                if cached and cached["actual"] is None:
                    return {"decision": "hold", "reason": "duplicate_unsettled_operation"}
            count = self.db.execute(
                "SELECT count(*) FROM requests WHERE workflow=?", (w["id"],)
            ).fetchone()[0]
            retries = self.db.execute(
                "SELECT count(*) FROM events WHERE workflow=? AND kind='retry_admitted'", (w["id"],)
            ).fetchone()[0]
            if not reason and count >= cfg["max_iterations"]:
                reason = "iteration_limit"
            if not reason and retry and retries >= cfg["max_retries"]:
                reason = "retry_limit"
            ancestors, cursor = [], t
            while cursor:
                ancestors.append(cursor)
                cursor = (
                    self.db.execute(
                        "SELECT * FROM tasks WHERE id=?", (cursor["parent"],)
                    ).fetchone()
                    if cursor["parent"]
                    else None
                )
            for ancestor in ancestors:
                used = self.db.execute(
                    """SELECT coalesce(sum(coalesce(r.actual,r.reserved)),0)
                    FROM requests r JOIN allocations a ON r.id=a.request WHERE a.task=?""",
                    (ancestor["id"],),
                ).fetchone()[0]
                if used + reserve > ancestor["budget"] and not reason:
                    reason = "shared_budget"
            if reason:
                self.event(
                    w["id"],
                    t["id"],
                    "denied" if w["mode"] == "enforce" else "would_pause",
                    reason=reason,
                )
                if w["mode"] == "enforce" or reason == "workflow_not_running":
                    return {"decision": "pause", "reason": reason}
            self.db.execute(
                """INSERT INTO requests(id,workflow,task,fingerprint,model,category,
              estimated,reserved,state,cache_key,created) VALUES(?,?,?,?,?,?,?,?,'reserved',?,?)""",
                (
                    rid,
                    w["id"],
                    t["id"],
                    fingerprint,
                    model,
                    category,
                    estimate,
                    reserve,
                    key,
                    time.time(),
                ),
            )
            self.db.executemany(
                "INSERT INTO allocations VALUES(?,?)", [(rid, a["id"]) for a in ancestors]
            )
            self.event(
                w["id"],
                t["id"],
                "retry_admitted" if retry else "admitted",
                request_id=rid,
                reserved=dollars(reserve),
                reason=reason,
            )
            return {"decision": "continue", "request_id": rid, "reserved": dollars(reserve)}

    def settle(self, body):
        actual = amount(body["actual_cost"])
        valid = body.get("validated", False)
        if type(valid) is not bool:
            raise ValueError("validated must be boolean")
        result = canonical(body.get("result"))
        if len(result.encode()) > 131072:
            raise ValueError("Checkpoint exceeds 128 KiB")
        counts = body.get("usage", {})
        allowed = {
            "input_tokens",
            "output_tokens",
            "system",
            "history",
            "retrieval",
            "tool_schema",
            "user",
            "unattributed",
            "cached_input_tokens",
            "reasoning_tokens",
        }
        if (
            not isinstance(counts, dict)
            or set(counts) - allowed
            or any(type(v) is not int or v < 0 for v in counts.values())
        ):
            raise ValueError("Usage accepts only nonnegative named token counts")
        parts = {"system", "history", "retrieval", "tool_schema", "user"}
        attributed = sum(counts.get(k, 0) for k in parts)
        if set(counts) & parts and "input_tokens" not in counts:
            raise ValueError("Component attribution requires an input total")
        if "input_tokens" in counts:
            unknown = counts["input_tokens"] - attributed
            if unknown < 0 or counts.get("unattributed", unknown) != unknown:
                raise ValueError("Token attribution does not match the input total")
            counts = {**counts, "unattributed": unknown}
        usage = canonical(counts)
        with self.transaction():
            r = self.db.execute(
                "SELECT * FROM requests WHERE id=?", (body["request_id"],)
            ).fetchone()
            if not r:
                raise ValueError("Unknown reservation")
            if r["actual"] is not None:
                if (r["actual"], r["result"], r["validated"], r["usage"]) == (
                    actual,
                    result,
                    int(valid),
                    usage,
                ):
                    return {"status": r["state"]}
                raise ValueError("Conflicting settlement; use an explicit reconciliation")
            self.db.execute(
                "UPDATE requests SET "
                "actual=?,state='settled',result=?,validated=?,usage=?,settled=? WHERE id=?",
                (actual, result, int(valid), usage, time.time(), r["id"]),
            )
            self.event(
                r["workflow"], r["task"], "settled", request_id=r["id"], actual=dollars(actual)
            )
            if actual > r["reserved"]:
                self.db.execute("UPDATE workflows SET state='paused' WHERE id=?", (r["workflow"],))
                self.event(r["workflow"], r["task"], "bound_exceeded", request_id=r["id"])
            return {"status": "settled", "bound_exceeded": actual > r["reserved"]}

    def uncertain(self, body):
        with self.transaction():
            r = self.db.execute(
                "SELECT * FROM requests WHERE id=?", (body["request_id"],)
            ).fetchone()
            if not r:
                raise ValueError("Unknown reservation")
            if r["actual"] is None:
                self.db.execute("UPDATE requests SET state='uncertain' WHERE id=?", (r["id"],))
                self.event(r["workflow"], r["task"], "uncertain", request_id=r["id"])
        return {"status": "held"}

    def transition(self, body):
        action = body["action"]
        if action not in ("pause", "resume", "finish"):
            raise ValueError("Invalid workflow action")
        with self.transaction():
            w = self.db.execute(
                "SELECT * FROM workflows WHERE id=?", (body["workflow_id"],)
            ).fetchone()
            if not w:
                raise ValueError("Unknown workflow")
            if action == "finish":
                accepted = body.get("accepted")
                if type(accepted) is not bool:
                    raise ValueError("Explicit business acceptance is required")
                if w["finished"] is not None:
                    if w["accepted"] != int(accepted):
                        raise ValueError("Conflicting outcome")
                    return {"state": w["state"]}
                self.db.execute(
                    "UPDATE workflows SET state='completed',accepted=?,finished=? WHERE id=?",
                    (int(accepted), time.time(), w["id"]),
                )
            else:
                identity(body.get("reason"), "operator reason")
                if w["finished"] is not None:
                    raise ValueError("A finished workflow cannot resume")
                state = "paused" if action == "pause" else "running"
                self.db.execute("UPDATE workflows SET state=? WHERE id=?", (state, w["id"]))
            self.event(w["id"], w["id"], action, reason=body.get("reason"))
        return {"state": "completed" if action == "finish" else state}

    def progress(self, body):
        stage = identity(body["stage"], "stage")
        done, total = body["completed"], body["total"]
        if type(done) is not int or type(total) is not int or not 0 <= done <= total or total <= 0:
            raise ValueError("Progress requires 0 <= completed <= total")
        with self.transaction():
            t, w = self._task(body["task_id"])
            last = self.db.execute(
                "SELECT * FROM progress WHERE task=? AND stage=? ORDER BY id DESC LIMIT 1",
                (t["id"], stage),
            ).fetchone()
            if last and (done < last["done"] or total != last["total"]):
                raise ValueError("Progress cannot go backwards or change its total")
            self.db.execute(
                "INSERT INTO progress(workflow,task,stage,done,total,spent,created) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    w["id"],
                    t["id"],
                    stage,
                    done,
                    total,
                    None
                    if self.db.execute(
                        "SELECT 1 FROM requests WHERE workflow=? AND actual IS NULL", (w["id"],)
                    ).fetchone()
                    else self._charge(w["id"]),
                    time.time(),
                ),
            )
            self.event(w["id"], t["id"], "progress", stage=stage, completed=done, total=total)
        return {"status": "recorded"}

    def reconcile(self, body):
        """An immutable invoice revision chain; unmatched rows never invent attribution."""
        for k in ("provider", "invoice_id", "line_id", "request_id", "reference"):
            identity(body.get(k), k)
        if body.get("currency", "USD") != "USD":
            raise ValueError("Only USD supported")
        cost = amount(body["amount"])
        revision = body.get("revision", 1)
        if type(revision) is not int or revision < 1:
            raise ValueError("Invalid invoice revision")
        encoded = canonical(body)
        keys = (body["provider"], body["invoice_id"], body["line_id"])
        with self.transaction():
            old = self.db.execute(
                "SELECT * FROM bills WHERE provider=? AND invoice_id=? AND line_id=? ORDER BY "
                "revision DESC LIMIT 1",
                keys,
            ).fetchone()
            if old and old["revision"] == revision and old["body"] == encoded:
                return {"matched": bool(old["matched"]), "duplicate": True}
            if revision != (old["revision"] + 1 if old else 1):
                raise ValueError("Conflicting or nonsequential invoice revision")
            r = self.db.execute(
                "SELECT * FROM requests WHERE id=?", (body["request_id"],)
            ).fetchone()
            matched = bool(r and r["model"].split("/")[0] == body["provider"])
            if old and old["request_id"] != body["request_id"]:
                raise ValueError("Invoice revisions cannot reassign a request")
            self.db.execute(
                "INSERT INTO bills VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    *keys,
                    body["request_id"],
                    cost,
                    revision,
                    body["reference"],
                    encoded,
                    int(matched),
                ),
            )
            if matched:
                prior = self.db.execute(
                    "SELECT * FROM bills WHERE request_id=? AND matched=1 ORDER BY rowid DESC "
                    "LIMIT 2",
                    (r["id"],),
                ).fetchall()
                if (
                    len(prior) > 1
                    and (prior[1]["provider"], prior[1]["invoice_id"], prior[1]["line_id"]) != keys
                ):
                    raise ValueError("A request already has a different bill line")
                self.db.execute(
                    "UPDATE requests SET actual=?,state='reconciled',settled=? WHERE id=?",
                    (cost, time.time(), r["id"]),
                )
                self.event(
                    r["workflow"],
                    r["task"],
                    "reconciled",
                    request_id=r["id"],
                    previous=dollars(r["actual"]) if r["actual"] is not None else None,
                    amount=dollars(cost),
                    reference=body["reference"],
                    revision=revision,
                )
                if cost > r["reserved"]:
                    self.db.execute(
                        "UPDATE workflows SET state='paused' WHERE id=?", (r["workflow"],)
                    )
            return {"matched": matched, "duplicate": False}

    def forecast(self, workflow):
        with self.lock:
            w = self.db.execute("SELECT * FROM workflows WHERE id=?", (workflow,)).fetchone()
            if not w:
                raise ValueError("Unknown workflow")
            p = self.db.execute(
                "SELECT * FROM progress WHERE task=? ORDER BY id DESC LIMIT 1", (workflow,)
            ).fetchone()
            values = []
            if p:
                candidates = self.db.execute(
                    """SELECT p.*,w.id wid FROM progress p JOIN workflows w ON w.id=p.workflow
                  WHERE w.id!=? AND w.customer=? AND w.task_type=? AND w.policy=? AND
                  w.state='completed'
                  AND p.spent IS NOT NULL AND p.task=w.id AND p.stage=?
                  AND p.total=? AND p.done=? ORDER BY p.id DESC""",
                    (
                        workflow,
                        w["customer"],
                        w["task_type"],
                        w["policy"],
                        p["stage"],
                        p["total"],
                        p["done"],
                    ),
                ).fetchall()
                seen = set()
                for row in candidates:
                    if row["wid"] in seen:
                        continue
                    seen.add(row["wid"])
                    if self.db.execute(
                        "SELECT 1 FROM requests WHERE workflow=? AND actual IS NULL", (row["wid"],)
                    ).fetchone():
                        continue
                    values.append(max(0, self._charge(row["wid"]) - row["spent"]))
                    if len(values) == 200:
                        break
            result = {
                "samples": len(values),
                "remaining_p50": None,
                "remaining_p90": None,
                "advisory_only": True,
                "advice": "insufficient_history",
                "method": "Empirical residual cost at matching task/policy/stage/progress; not "
                "a confidence interval",
            }
            if len(values) >= 30:
                values.sort()
                p50, p90 = (
                    values[math.ceil(len(values) * 0.5) - 1],
                    values[math.ceil(len(values) * 0.9) - 1],
                )
                result.update(
                    remaining_p50=dollars(p50),
                    remaining_p90=dollars(p90),
                    advice="within_cost_target",
                )
                target = json.loads(w["config"])["max_cost_per_accepted_result"]
                if target is not None and self._charge(workflow) + p90 > amount(target):
                    result["advice"] = "review_expected_cost"
            return result

    def status(self, workflow=None):
        with self.lock:
            ws = self.db.execute(
                "SELECT * FROM workflows"
                + (" WHERE id=?" if workflow else "")
                + " ORDER BY created DESC",
                (workflow,) if workflow else (),
            ).fetchall()
            result = []
            for w in ws:
                requests = self.db.execute(
                    "SELECT * FROM requests WHERE workflow=?", (w["id"],)
                ).fetchall()
                tasks = self.db.execute(
                    "SELECT * FROM tasks WHERE workflow=?", (w["id"],)
                ).fetchall()
                known = sum(r["actual"] for r in requests if r["actual"] is not None)
                pending = sum(r["reserved"] for r in requests if r["actual"] is None)
                result.append(
                    {
                        "workflow_id": w["id"],
                        "customer_id": w["customer"],
                        "task_type": w["task_type"],
                        "policy_version": w["policy"],
                        "mode": w["mode"],
                        "state": w["state"],
                        "budget": dollars(w["budget"]),
                        "known_cost": dollars(known),
                        "reserved_cost": dollars(pending),
                        "bill_reconciled_cost": dollars(
                            sum(r["actual"] for r in requests if r["state"] == "reconciled")
                        ),
                        "pending_requests": sum(r["actual"] is None for r in requests),
                        "accepted": None if w["accepted"] is None else bool(w["accepted"]),
                        "cost_per_accepted_result": dollars(known)
                        if w["accepted"]
                        and not pending
                        and all(r["actual"] is not None for r in requests)
                        else None,
                        "tasks": [
                            {
                                "task_id": t["id"],
                                "parent_task_id": t["parent"],
                                "budget": dollars(t["budget"]),
                            }
                            for t in tasks
                        ],
                        "requests": [
                            {
                                "request_id": r["id"],
                                "task_id": r["task"],
                                "model": r["model"],
                                "category": r["category"],
                                "state": r["state"],
                                "estimated_cost": dollars(r["estimated"]),
                                "reserved_cost": dollars(r["reserved"]),
                                "actual_cost": dollars(r["actual"])
                                if r["actual"] is not None
                                else None,
                                "usage": json.loads(r["usage"]) if r["usage"] else {},
                            }
                            for r in requests
                        ],
                        "events": [
                            {
                                "kind": e["kind"],
                                "task_id": e["task"],
                                "payload": json.loads(e["payload"]),
                                "created": e["created"],
                            }
                            for e in self.db.execute(
                                "SELECT * FROM events WHERE workflow=? ORDER BY id DESC LIMIT 100",
                                (w["id"],),
                            )
                        ],
                        "forecast": self.forecast(w["id"]),
                    }
                )
            return {
                "workflows": result,
                "unmatched_bill_lines": self.db.execute(
                    """SELECT count(*) FROM bills b WHERE matched=0 AND revision=(
                    SELECT max(revision) FROM bills latest WHERE latest.provider=b.provider
                    AND latest.invoice_id=b.invoice_id AND latest.line_id=b.line_id)"""
                ).fetchone()[0],
            }

    def close(self):
        self.db.close()
        self.lockfile.close()
