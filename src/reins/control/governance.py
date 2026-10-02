"""Durable, opt-in controls layered on the single-host transaction boundary.

All admission hooks run inside Ledger.reserve's transaction. This is a trusted
local integration, not a sandbox for arbitrary Python or a multi-tenant service.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def validate_fields(payload, fields):
    """Small explicit schema: required fields and scalar types, no coercion."""
    types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    if not isinstance(payload, dict) or not isinstance(fields, dict):
        raise ValueError("Expected structured object")
    if set(payload) != set(fields):
        raise ValueError("Missing or unexpected fields")
    for name, kind in fields.items():
        if kind not in types or type(payload[name]) not in (
            types[kind] if isinstance(types[kind], tuple) else (types[kind],)
        ):
            raise ValueError(f"Invalid field: {name}")
    encoded(payload)


class Governance:
    def migrate(self, path):
        version = self.db.execute("SELECT max(version) FROM schema_version").fetchone()[0]
        if version > 2:
            raise ValueError("Ledger was created by a newer Reins version")
        if version == 2:
            return
        backup = Path(str(path) + ".pre-v2.sqlite")
        # Atomic SQLite backup also includes committed WAL data.
        temp = Path(str(backup) + ".tmp")
        with sqlite3.connect(temp) as destination:
            self.db.backup(destination)
        os.chmod(temp, 0o600)
        os.replace(temp, backup)
        statements = [
            "CREATE TABLE pools(id TEXT PRIMARY KEY, customer TEXT NOT NULL, team TEXT NOT NULL, "
            "budget INTEGER NOT NULL, critical INTEGER NOT NULL)",
            "CREATE TABLE memberships(workflow TEXT PRIMARY KEY REFERENCES workflows(id), "
            "pool TEXT REFERENCES pools(id), team TEXT NOT NULL, priority TEXT NOT NULL)",
            "CREATE TABLE operation_meta(request TEXT PRIMARY KEY REFERENCES requests(id), "
            "signature TEXT, phase TEXT NOT NULL, tool TEXT, success INTEGER)",
            "CREATE TABLE tools(name TEXT PRIMARY KEY, policy TEXT NOT NULL, digest TEXT NOT NULL)",
            "CREATE TABLE approvals(id TEXT PRIMARY KEY, task TEXT REFERENCES tasks(id), "
            "tool TEXT, arguments TEXT, policy TEXT, expires REAL, state TEXT, request TEXT)",
            "CREATE TABLE shared_state(workflow TEXT REFERENCES workflows(id), name TEXT, "
            "version INTEGER, payload TEXT, source TEXT, expires REAL, "
            "PRIMARY KEY(workflow,name,version))",
            "CREATE TABLE handoffs(id TEXT PRIMARY KEY, sender TEXT REFERENCES tasks(id), "
            "receiver TEXT REFERENCES tasks(id), payload TEXT, state_name TEXT, version INTEGER)",
            "CREATE TABLE economics(id TEXT PRIMARY KEY, workflow TEXT REFERENCES workflows(id), "
            "kind TEXT, amount INTEGER, reference TEXT)",
            "CREATE TABLE context_samples(id TEXT PRIMARY KEY, "
            "workflow TEXT REFERENCES workflows(id), "
            "revision TEXT, counts TEXT, provider_input INTEGER, created REAL)",
        ]
        with self.transaction():
            for statement in statements:
                self.db.execute(statement)
            self.db.execute("DELETE FROM schema_version")
            self.db.execute("INSERT INTO schema_version VALUES(2)")

    def governance_config(self, body):
        from .ledger import amount, identity

        cfg = {}
        for key in (
            "max_depth",
            "max_tools",
            "failure_limit",
            "repeat_limit",
            "repeat_window_seconds",
        ):
            if key in body:
                if type(body[key]) is not int or not 0 <= body[key] <= 100000:
                    raise ValueError(f"Invalid {key}")
                cfg[key] = body[key]
        if "deadline" in body:
            if type(body["deadline"]) not in (int, float) or not 0 < body["deadline"] < 1e12:
                raise ValueError("deadline must be a finite Unix timestamp")
            cfg["deadline"] = body["deadline"]
        if "wrapup_reserve" in body:
            if amount(body["wrapup_reserve"]) > amount(body["budget"]):
                raise ValueError("Wrap-up reserve exceeds workflow budget")
            cfg["wrapup_reserve"] = body["wrapup_reserve"]
        if "allowed_tools" in body:
            if not isinstance(body["allowed_tools"], list):
                raise ValueError("allowed_tools must be a list")
            cfg["allowed_tools"] = [identity(v, "tool") for v in body["allowed_tools"]]
        for key in ("pool_id", "team_id"):
            if key in body:
                cfg[key] = identity(body[key], key)
        if "priority" in body:
            if body["priority"] not in ("normal", "critical"):
                raise ValueError("Invalid priority")
            cfg["priority"] = body["priority"]
        return cfg

    def pool(self, body):
        from .ledger import amount, dollars, identity

        pid = identity(body["pool_id"], "pool")
        values = (
            identity(body["customer_id"], "customer"),
            identity(body["team_id"], "team"),
            amount(body["budget"]),
            amount(body.get("critical_reserve", 0)),
        )
        if values[3] > values[2]:
            raise ValueError("Critical reserve exceeds budget")
        with self.transaction():
            old = self.db.execute("SELECT * FROM pools WHERE id=?", (pid,)).fetchone()
            if old and tuple(old)[1:] != values:
                raise ValueError("Pool identity and allocation are immutable; create a new period")
            self.db.execute("INSERT OR IGNORE INTO pools VALUES(?,?,?,?,?)", (pid, *values))
        return {"pool_id": pid, "budget": dollars(values[2])}

    def join_pool(self, wid, customer, cfg):
        pool = cfg.get("pool_id")
        team = cfg.get("team_id", "default")
        if pool:
            p = self.db.execute("SELECT * FROM pools WHERE id=?", (pool,)).fetchone()
            if not p or (p["customer"], p["team"]) != (customer, team):
                raise ValueError("Pool customer/team does not match workflow")
        self.db.execute(
            "INSERT INTO memberships VALUES(?,?,?,?)",
            (wid, pool, team, cfg.get("priority", "normal")),
        )

    def branch_limit(self, parent, cfg):
        depth, cursor = 1, parent
        while cursor["parent"]:
            depth += 1
            cursor = self.db.execute(
                "SELECT * FROM tasks WHERE id=?", (cursor["parent"],)
            ).fetchone()
        if "max_depth" in cfg and depth > cfg["max_depth"]:
            return "depth_limit"
        if time.time() >= cfg.get("deadline", float("inf")):
            return "deadline"

    def admission_check(self, body, t, w, cfg, reserve):
        from .ledger import amount

        phase = body.get("phase", "work")
        if phase not in ("work", "wrapup"):
            raise ValueError("Unknown execution phase")
        if w["state"] == "wrapping" and phase != "wrapup":
            return "wrapup_only"
        if phase == "wrapup" and w["state"] != "wrapping":
            return "wrapup_not_started"
        # Wrapping is an explicit terminal work stage, allowed after a work deadline.
        if phase == "work" and time.time() >= cfg.get("deadline", float("inf")):
            return "deadline"
        if phase == "work" and self._charge(w["id"]) + reserve > (
            w["budget"] - amount(cfg.get("wrapup_reserve", 0))
        ):
            return "shared_budget"
        if phase == "wrapup":
            if self.db.execute(
                "SELECT 1 FROM operation_meta m JOIN requests r ON r.id=m.request "
                "WHERE r.workflow=? AND m.phase='wrapup'",
                (w["id"],),
            ).fetchone():
                return "wrapup_already_admitted"
            return None  # Total task/workflow/pool budgets still checked by caller.
        if "max_tools" in cfg and body.get("category", "model") == "tool":
            n = self.db.execute(
                "SELECT count(*) FROM requests WHERE workflow=? AND category='tool'", (w["id"],)
            ).fetchone()[0]
            if n >= cfg["max_tools"]:
                return "tool_limit"
        if cfg.get("failure_limit") is not None:
            outcomes = {
                r["request"]: r["success"]
                for r in self.db.execute(
                    "SELECT m.request,m.success FROM operation_meta m JOIN requests r "
                    "ON r.id=m.request WHERE r.task=? AND m.success IS NOT NULL",
                    (t["id"],),
                )
            }
            events = self.db.execute(
                "SELECT payload FROM events WHERE task=? AND kind IN ('settled','uncertain') "
                "ORDER BY id DESC",
                (t["id"],),
            )
            failures, seen = 0, set()
            for event in events:
                rid = json.loads(event["payload"])["request_id"]
                if rid in seen or rid not in outcomes:
                    continue
                seen.add(rid)
                if outcomes[rid]:
                    break
                failures += 1
            if failures >= cfg["failure_limit"]:
                return "consecutive_failures"
        signature = body.get("operation_key")
        if signature is not None:
            if not isinstance(signature, str) or len(signature) != 64:
                raise ValueError("operation_key must be a SHA-256 digest")
            n = self.db.execute(
                "SELECT count(*) FROM operation_meta m JOIN requests r ON r.id=m.request "
                "WHERE r.task=? AND m.signature=? AND r.created>=?",
                (t["id"], signature, time.time() - cfg.get("repeat_window_seconds", 60)),
            ).fetchone()[0]
            if "repeat_limit" in cfg and n >= cfg["repeat_limit"]:
                return "repeated_operation"
        return None

    def pool_check(self, workflow, reserve):
        m = self.db.execute("SELECT * FROM memberships WHERE workflow=?", (workflow,)).fetchone()
        if not m or not m["pool"]:
            return None
        p = self.db.execute("SELECT * FROM pools WHERE id=?", (m["pool"],)).fetchone()
        rows = self.db.execute(
            "SELECT m.priority,coalesce(sum(coalesce(r.actual,r.reserved)),0) used "
            "FROM memberships m LEFT JOIN requests r ON r.workflow=m.workflow "
            "WHERE m.pool=? GROUP BY m.priority",
            (p["id"],),
        ).fetchall()
        used = {r["priority"]: r["used"] for r in rows}
        if sum(used.values()) + reserve > p["budget"]:
            return "pool_budget"
        if (
            m["priority"] == "normal"
            and used.get("normal", 0) + reserve > p["budget"] - p["critical"]
        ):
            return "critical_reserve_protected"

    def register_tool(self, body):
        from .ledger import identity

        name = identity(body["name"], "tool")
        policy = {
            "fields": body.get("fields", {}),
            "read_only": body.get("read_only", True),
            "approval_required": body.get("approval_required", False),
            "allowed_values": body.get("allowed_values", {}),
        }
        if type(policy["read_only"]) is not bool or type(policy["approval_required"]) is not bool:
            raise ValueError("Tool flags must be boolean")
        if not isinstance(policy["fields"], dict) or any(
            k not in ("string", "integer", "number", "boolean", "object", "array")
            for k in policy["fields"].values()
        ):
            raise ValueError("Invalid tool fields")
        if not isinstance(policy["allowed_values"], dict) or any(
            k not in policy["fields"] or not isinstance(v, list)
            for k, v in policy["allowed_values"].items()
        ):
            raise ValueError("Invalid tool argument allowlist")
        digest = fingerprint(policy)
        with self.transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO tools VALUES(?,?,?)", (name, encoded(policy), digest)
            )
        return {"name": name, "policy_digest": digest}

    def tool_check(self, body, t, cfg):
        tool = body.get("tool_name")
        if not tool:
            if body.get("category") == "tool" and "allowed_tools" in cfg:
                raise ValueError("Registered tool required")
            return None
        row = self.db.execute("SELECT * FROM tools WHERE name=?", (tool,)).fetchone()
        if not row or tool not in cfg.get("allowed_tools", []):
            raise ValueError("Tool not allowed for this workflow")
        policy = json.loads(row["policy"])
        args = body.get("tool_arguments", {})
        validate_fields(args, policy["fields"])
        for field, values in policy["allowed_values"].items():
            if args[field] not in values:
                raise ValueError("Tool argument outside allowed scope")
        if body.get("read_only", False) and not policy["read_only"]:
            raise ValueError("Write tool cannot be declared read-only")
        if policy["approval_required"] or not policy["read_only"]:
            a = self.db.execute(
                "SELECT * FROM approvals WHERE id=?", (body.get("approval_id"),)
            ).fetchone()
            if (
                not a
                or (a["task"], a["tool"], a["arguments"], a["policy"])
                != (t["id"], tool, fingerprint(args), row["digest"])
                or a["state"] != "approved"
                or a["expires"] <= time.time()
            ):
                raise ValueError("An unexpired approval for these exact arguments is required")
            return a["id"]

    def approval(self, body):
        from .ledger import identity

        aid = identity(body["approval_id"], "approval")
        with self.transaction():
            t, w = self._task(body["task_id"])
            tool = self.db.execute(
                "SELECT * FROM tools WHERE name=?", (body["tool_name"],)
            ).fetchone()
            if not tool or tool["name"] not in json.loads(w["config"]).get("allowed_tools", []):
                raise ValueError("Tool not allowed")
            args = body["arguments"]
            policy = json.loads(tool["policy"])
            validate_fields(args, policy["fields"])
            for field, values in policy["allowed_values"].items():
                if args[field] not in values:
                    raise ValueError("Tool argument outside allowed scope")
            ttl = body.get("ttl_seconds", 300)
            if type(ttl) is not int or not 1 <= ttl <= 3600:
                raise ValueError("Approval TTL must be 1..3600 seconds")
            old = self.db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
            if old:
                if (old["task"], old["tool"], old["arguments"], old["policy"]) != (
                    t["id"],
                    tool["name"],
                    fingerprint(args),
                    tool["digest"],
                ):
                    raise ValueError("Approval identity cannot be rebound")
                return {"approval_id": aid, "state": old["state"]}
            self.db.execute(
                "INSERT INTO approvals VALUES(?,?,?,?,?,?,'approved',NULL)",
                (aid, t["id"], tool["name"], fingerprint(args), tool["digest"], time.time() + ttl),
            )
            self.event(w["id"], t["id"], "approved", approval_id=aid, tool=tool["name"])
        return {"approval_id": aid, "state": "approved"}

    def state_write(self, body):
        from .ledger import identity

        name = identity(body["name"], "state name")
        source = identity(body["source"], "source")
        ttl = body.get("ttl_seconds", 3600)
        version = body["expected_version"]
        if (
            type(version) is not int
            or version < 0
            or type(ttl) is not int
            or not 1 <= ttl <= 2592000
        ):
            raise ValueError("Invalid state version or expiry")
        payload = encoded(body["payload"])
        if len(payload.encode()) > 131072:
            raise ValueError("State exceeds 128 KiB")
        with self.transaction():
            t, w = self._task(body["task_id"])
            last = (
                self.db.execute(
                    "SELECT max(version) FROM shared_state WHERE workflow=? AND name=?",
                    (w["id"], name),
                ).fetchone()[0]
                or 0
            )
            if last != version:
                raise ValueError("State conflict: read latest version before editing")
            self.db.execute(
                "INSERT INTO shared_state VALUES(?,?,?,?,?,?)",
                (w["id"], name, version + 1, payload, source, time.time() + ttl),
            )
            self.event(w["id"], t["id"], "state_updated", name=name, version=version + 1)
        return {"version": version + 1}

    def state_read(self, body):
        with self.lock:
            _, w = self._task(body["task_id"])
            row = self.db.execute(
                "SELECT * FROM shared_state WHERE workflow=? AND name=? "
                "ORDER BY version DESC LIMIT 1",
                (w["id"], body["name"]),
            ).fetchone()
            if not row or row["expires"] <= time.time():
                raise ValueError("State missing or expired")
            return {
                "version": row["version"],
                "payload": json.loads(row["payload"]),
                "source": row["source"],
                "expires": row["expires"],
            }

    def handoff(self, body):
        from .ledger import identity

        hid = identity(body["handoff_id"], "handoff")
        validate_fields(
            body["payload"], {"goal": "string", "inputs": "object", "completed": "array"}
        )
        with self.transaction():
            sender, sw = self._task(body["task_id"])
            receiver, rw = self._task(body["receiver_task_id"])
            if sw["id"] != rw["id"]:
                raise ValueError("Handoff must stay inside the workflow")
            state = self.state_read({"task_id": sender["id"], "name": body["state_name"]})
            if state["version"] != body["version"]:
                raise ValueError("Handoff state is stale")
            values = (
                hid,
                sender["id"],
                receiver["id"],
                encoded(body["payload"]),
                body["state_name"],
                state["version"],
            )
            old = self.db.execute("SELECT * FROM handoffs WHERE id=?", (hid,)).fetchone()
            if old and tuple(old) != values:
                raise ValueError("Handoff identity cannot be rebound")
            self.db.execute("INSERT OR IGNORE INTO handoffs VALUES(?,?,?,?,?,?)", values)
            self.event(sw["id"], sender["id"], "handoff", receiver=receiver["id"], handoff_id=hid)
        return {"context": self.context(receiver["id"]), "state": state, "payload": body["payload"]}

    def economic_entry(self, body):
        from .ledger import amount, identity

        kind = body["kind"]
        if kind not in ("human_review", "infrastructure", "revenue", "coverage_complete"):
            raise ValueError("Invalid economics category")
        value = body.get("amount", 0)
        if kind == "human_review":
            hours = Decimal(str(body["hours"]))
            rate = Decimal(str(body["hourly_rate"]))
            if not hours.is_finite() or not rate.is_finite() or hours < 0 or rate < 0:
                raise ValueError("Invalid hours or hourly rate")
            value = hours * rate
        if kind == "coverage_complete" and amount(value) != 0:
            raise ValueError("Coverage assertion has no cost")
        values = (
            identity(body["entry_id"], "entry"),
            body["workflow_id"],
            kind,
            amount(value),
            identity(body["reference"], "reference"),
        )
        with self.transaction():
            self._task(body["workflow_id"])
            old = self.db.execute("SELECT * FROM economics WHERE id=?", (values[0],)).fetchone()
            if old and tuple(old) != values:
                raise ValueError("Economics entry is immutable")
            self.db.execute("INSERT OR IGNORE INTO economics VALUES(?,?,?,?,?)", values)
        return {"status": "recorded"}

    def context_sample(self, body):
        from .ledger import identity

        counts = body["components"]
        allowed = {"system", "history", "retrieval", "tool_schema", "user", "unattributed"}
        if (
            not isinstance(counts, dict)
            or set(counts) - allowed
            or any(type(v) is not int or v < 0 for v in counts.values())
        ):
            raise ValueError("Invalid component estimates")
        actual = body.get("provider_input_tokens")
        if actual is not None and (type(actual) is not int or actual < 0):
            raise ValueError("Invalid provider total")
        with self.transaction():
            t, w = self._task(body["task_id"])
            values = (
                identity(body["sample_id"], "sample"),
                w["id"],
                identity(body["revision"], "revision"),
                encoded(counts),
                actual,
            )
            old = self.db.execute(
                "SELECT * FROM context_samples WHERE id=?", (values[0],)
            ).fetchone()
            if old and tuple(old)[:5] != values:
                raise ValueError("Context sample cannot be rebound")
            self.db.execute(
                "INSERT OR IGNORE INTO context_samples VALUES(?,?,?,?,?,?)", (*values, time.time())
            )
        return {"status": "recorded", "components_are_estimates": True}

    def governance_status(self, result):
        from .ledger import dollars

        with self.lock:
            for w in result["workflows"]:
                wid = w["workflow_id"]
                m = self.db.execute("SELECT * FROM memberships WHERE workflow=?", (wid,)).fetchone()
                w.update(team_id=m["team"] if m else "default", pool_id=m["pool"] if m else None)
                entries = self.db.execute(
                    "SELECT * FROM economics WHERE workflow=?", (wid,)
                ).fetchall()
                extras = sum(
                    e["amount"] for e in entries if e["kind"] in ("human_review", "infrastructure")
                )
                revenues = [e for e in entries if e["kind"] == "revenue"]
                complete = (
                    any(e["kind"] == "coverage_complete" for e in entries)
                    and not w["pending_requests"]
                )
                total = Decimal(w["known_cost"]) + Decimal(dollars(extras))
                revenue = sum(e["amount"] for e in revenues)
                w["economics"] = {
                    "recorded_total_cost": str(total),
                    "coverage_complete": complete,
                    "revenue": dollars(revenue) if revenues else None,
                    "margin": str(Decimal(dollars(revenue)) - total)
                    if complete and revenues
                    else None,
                }
                w["context_samples"] = [
                    {
                        "revision": r["revision"],
                        "components": json.loads(r["counts"]),
                        "provider_input_tokens": r["provider_input"],
                        "components_are_estimates": True,
                    }
                    for r in self.db.execute(
                        "SELECT * FROM context_samples WHERE workflow=? "
                        "ORDER BY created DESC LIMIT 50",
                        (wid,),
                    )
                ]
                cfg = json.loads(
                    self.db.execute("SELECT config FROM workflows WHERE id=?", (wid,)).fetchone()[0]
                )
                remaining = max(
                    Decimal(0),
                    Decimal(w["budget"]) - Decimal(w["known_cost"]) - Decimal(w["reserved_cost"]),
                )
                w["budget_state"] = {
                    "remaining": str(remaining),
                    "work_remaining": str(
                        max(Decimal(0), remaining - Decimal(str(cfg.get("wrapup_reserve", 0))))
                    ),
                    "wrapup_reserve": str(cfg.get("wrapup_reserve", 0)),
                }
            result["pools"] = []
            for p in self.db.execute("SELECT * FROM pools"):
                used = self.db.execute(
                    "SELECT coalesce(sum(coalesce(r.actual,r.reserved)),0) "
                    "FROM requests r JOIN memberships m ON m.workflow=r.workflow WHERE m.pool=?",
                    (p["id"],),
                ).fetchone()[0]
                result["pools"].append(
                    {
                        "pool_id": p["id"],
                        "customer_id": p["customer"],
                        "team_id": p["team"],
                        "budget": dollars(p["budget"]),
                        "committed": dollars(used),
                        "critical_reserve": dollars(p["critical"]),
                    }
                )
            groups = {}
            for w in result["workflows"]:
                key = (w["customer_id"], w["team_id"])
                g = groups.setdefault(
                    key,
                    {
                        "customer_id": key[0],
                        "team_id": key[1],
                        "recorded_cost": Decimal(0),
                        "reserved_cost": Decimal(0),
                        "accepted": 0,
                        "pending_requests": 0,
                    },
                )
                g["recorded_cost"] += Decimal(w["economics"]["recorded_total_cost"])
                g["reserved_cost"] += Decimal(w["reserved_cost"])
                g["accepted"] += int(w["accepted"] is True)
                g["pending_requests"] += w["pending_requests"]
            result["rollups"] = [
                {
                    **g,
                    "recorded_cost": str(g["recorded_cost"]),
                    "reserved_cost": str(g["reserved_cost"]),
                    "cost_per_accepted_result": str(g["recorded_cost"] / g["accepted"])
                    if g["accepted"] and not g["pending_requests"]
                    else None,
                }
                for g in groups.values()
            ]
        return result

    def regression_case(self, body):
        """Export recorded path and costs, without raw arguments or provider payloads."""
        from .ledger import dollars

        with self.lock:
            _, w = self._task(body["workflow_id"])
            rows = self.db.execute(
                "SELECT r.*,m.tool,m.success FROM requests r LEFT JOIN operation_meta m "
                "ON m.request=r.id WHERE r.workflow=? ORDER BY r.created,r.rowid",
                (w["id"],),
            ).fetchall()
            trajectory = []
            for r in rows:
                if r["tool"]:
                    approval = self.db.execute(
                        "SELECT id FROM approvals WHERE request=? AND state='consumed'", (r["id"],)
                    ).fetchone()
                    trajectory.append(
                        {
                            "kind": "tool",
                            "tool": r["tool"],
                            "approval_id": approval[0] if approval else None,
                            "approval_valid": bool(approval),
                            "success": bool(r["success"]),
                        }
                    )
            for event in self.db.execute(
                "SELECT payload FROM events WHERE workflow=? AND kind='validation' ORDER BY id",
                (w["id"],),
            ):
                trajectory.append({"kind": "validation", **json.loads(event[0])})
            complete = all(r["actual"] is not None for r in rows)
            return {
                "case_id": body.get("case_id", w["id"]),
                "accepted": w["accepted"] == 1 and w["finished"] is not None,
                "cost": dollars(sum(r["actual"] for r in rows)) if complete else None,
                "latency_ms": (w["finished"] - w["created"]) * 1000 if w["finished"] else None,
                "trajectory": trajectory,
            }
