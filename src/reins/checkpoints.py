"""Exact, tenant-isolated workflow checkpoints with explicit uncertain attempts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sqlite3
import time
from pathlib import Path


def fingerprint(value):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


class UnsettledAttempt(RuntimeError):
    """A prior attempt may have incurred charges; reconcile before retrying."""


class CheckpointStore:
    """Single-process writer. Only successful JSON results can be reused.

    A running/interrupted/failed call is not automatically retried. The caller
    explicitly resolves its billing before resetting it. Dependency revision
    digests propagate upstream input changes, even when output happens to match.
    """

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS checkpoints(
          key TEXT PRIMARY KEY, isolation TEXT, step TEXT, status TEXT,
          value TEXT, updated REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS checkpoint_events(
          id INTEGER PRIMARY KEY, key TEXT, action TEXT, details TEXT, created REAL);
        """)
        self.locks = {}

    def _event(self, key, action, details=None):
        self.conn.execute(
            "INSERT INTO checkpoint_events(key,action,details,created) VALUES (?,?,?,?)",
            (key, action, json.dumps(details or {}), time.time()),
        )
        self.conn.commit()

    async def run(self, *, isolation, step, version, inputs, execute, dependencies=()):
        if not all(isinstance(x, str) and x for x in (isolation, step, version)):
            raise ValueError("isolation, step and version are required")
        key = fingerprint([isolation, step, version, inputs, list(dependencies)])
        async with self.locks.setdefault(key, asyncio.Lock()):
            row = self.conn.execute("SELECT * FROM checkpoints WHERE key=?", (key,)).fetchone()
            if row and row["status"] == "succeeded":
                self._event(key, "reuse")
                return {"value": json.loads(row["value"]), "revision": key, "reused": True}
            if row and row["status"] != "retry_authorized":
                raise UnsettledAttempt(f"Unsettled checkpoint {key}; reconcile before retry")
            self.conn.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,'running',NULL,?,NULL) "
                "ON CONFLICT(key) DO UPDATE SET status='running',"
                "updated=excluded.updated,error=NULL",
                (key, isolation, step, time.time()),
            )
            self._event(key, "start")
            try:
                value = execute()
                if inspect.isawaitable(value):
                    value = await value
                encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
                self.conn.execute(
                    "UPDATE checkpoints SET status='succeeded',value=?,updated=? WHERE key=?",
                    (encoded, time.time(), key),
                )
                self._event(key, "complete")
                return {"value": value, "revision": key, "reused": False}
            except BaseException as exc:
                self.conn.execute(
                    "UPDATE checkpoints SET status='unsettled',error=?,updated=? WHERE key=?",
                    (type(exc).__name__, time.time(), key),
                )
                self._event(key, "unsettled", {"error": type(exc).__name__})
                raise

    def authorize_retry(self, key, *, reconciliation_reference):
        if not isinstance(reconciliation_reference, str) or not reconciliation_reference.strip():
            raise ValueError("Provide a billing reconciliation reference")
        row = self.conn.execute("SELECT status FROM checkpoints WHERE key=?", (key,)).fetchone()
        if row is None or row["status"] not in ("unsettled", "running"):
            raise ValueError("Only interrupted/unsettled attempts can be reset")
        if key in self.locks and self.locks[key].locked():
            raise RuntimeError("Cannot reset an active call")
        self.conn.execute("UPDATE checkpoints SET status='retry_authorized' WHERE key=?", (key,))
        self._event(key, "reconciled", {"reference": reconciliation_reference})

    def close(self):
        self.conn.close()
