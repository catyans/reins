"""DuckDB storage layer."""

from __future__ import annotations

import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from reins.core.models import RunData, SpanData

_SCHEMA_VERSION = 3
_OPEN = weakref.WeakValueDictionary()
_OPEN_LOCK = threading.RLock()

_MIGRATIONS: dict[int, str] = {
    3: """
    CREATE TABLE runtime_instances (
        instance_id VARCHAR PRIMARY KEY, heartbeat VARCHAR NOT NULL, stopped BOOLEAN NOT NULL
    );
    CREATE SEQUENCE run_event_sequence;
    CREATE TABLE run_events (
        seq BIGINT DEFAULT nextval('run_event_sequence') PRIMARY KEY,
        run_id VARCHAR NOT NULL, span_id VARCHAR, event_type VARCHAR NOT NULL,
        timestamp VARCHAR NOT NULL, payload VARCHAR NOT NULL
    );
    CREATE INDEX run_events_run ON run_events(run_id);
    UPDATE schema_version SET version=3;
    """,
    2: """
    CREATE TABLE budget_requests (
        span_id VARCHAR PRIMARY KEY, run_id VARCHAR, agent VARCHAR,
        reserved DECIMAL(28,12), actual DECIMAL(28,12), status VARCHAR,
        created_at VARCHAR, model VARCHAR, mode VARCHAR
    );
    CREATE TABLE budget_allocations (span_id VARCHAR, scope VARCHAR,
        PRIMARY KEY(span_id, scope));
    CREATE TABLE external_costs (id VARCHAR PRIMARY KEY, run_id VARCHAR,
        label VARCHAR, amount DECIMAL(28,12));
    CREATE TABLE outcomes (run_id VARCHAR PRIMARY KEY, success BOOLEAN NOT NULL,
        score DOUBLE, reason VARCHAR, recorded_at VARCHAR NOT NULL);
    UPDATE schema_version SET version=2;
    """,
    1: """
    CREATE TABLE IF NOT EXISTS runs (
        run_id          VARCHAR PRIMARY KEY,
        session_id      VARCHAR,
        agent_name      VARCHAR NOT NULL,
        status          VARCHAR NOT NULL,
        started_at      VARCHAR NOT NULL,
        ended_at        VARCHAR,
        total_cost      DOUBLE,
        total_tokens_in  BIGINT,
        total_tokens_out BIGINT,
        budget_limit    DOUBLE,
        degraded_count  INTEGER DEFAULT 0,
        metadata        VARCHAR
    );

    CREATE TABLE IF NOT EXISTS spans (
        span_id         VARCHAR PRIMARY KEY,
        run_id          VARCHAR NOT NULL,
        parent_span_id  VARCHAR,
        span_type       VARCHAR NOT NULL,
        name            VARCHAR NOT NULL,
        started_at      VARCHAR NOT NULL,
        ended_at        VARCHAR,
        duration_ms     DOUBLE,
        model           VARCHAR,
        model_requested VARCHAR,
        provider        VARCHAR,
        tokens_in       BIGINT,
        tokens_out      BIGINT,
        cost            DOUBLE,
        degraded        BOOLEAN DEFAULT false,
        tool_name       VARCHAR,
        tool_status     VARCHAR,
        tool_error      VARCHAR,
        context_tokens  BIGINT,
        context_health  DOUBLE,
        eval_scores     VARCHAR,
        safety_flags    VARCHAR,
        status          VARCHAR NOT NULL,
        error_message   VARCHAR,
        metadata        VARCHAR
    );

    CREATE TABLE IF NOT EXISTS budget_ledger (
        id              VARCHAR PRIMARY KEY,
        timestamp       VARCHAR NOT NULL,
        agent_name      VARCHAR NOT NULL,
        event_type      VARCHAR NOT NULL,
        amount          DOUBLE,
        balance_after   DOUBLE,
        run_id          VARCHAR,
        span_id         VARCHAR,
        details         VARCHAR
    );

    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    );

    INSERT INTO schema_version VALUES (1);
    """,
}


def _serialized(fn):
    from functools import wraps

    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapped


class Storage:
    """DuckDB-backed storage for traces, runs, and budget events."""

    DEFAULT_PATH = Path.home() / ".reins" / "data" / "traces.duckdb"

    def __init__(self, db_path: Path | str | None = None):
        self._path = Path(db_path) if db_path else self.DEFAULT_PATH
        self._lock = threading.RLock()
        self._closed = False
        self._path = self._path.resolve()
        with _OPEN_LOCK:
            if str(self._path) in _OPEN:
                raise RuntimeError("Use one Reins Storage writer per database in this process")
            _OPEN[str(self._path)] = self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self._path))
        self._migrate()

    def _migrate(self) -> None:
        current = self._get_schema_version()
        for version in sorted(_MIGRATIONS.keys()):
            if version > current:
                self._conn.execute("BEGIN TRANSACTION")
                try:
                    for statement in _MIGRATIONS[version].split(";"):
                        statement = statement.strip()
                        if statement:
                            self._conn.execute(statement)
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

    def _get_schema_version(self) -> int:
        try:
            result = self._conn.execute("SELECT version FROM schema_version").fetchone()
            return result[0] if result else 0
        except duckdb.CatalogException:
            return 0

    @_serialized
    def insert_run(self, run: RunData) -> None:
        self._conn.execute(
            """INSERT INTO runs (run_id, session_id, agent_name, status, started_at,
               ended_at, total_cost, total_tokens_in, total_tokens_out, budget_limit,
               degraded_count, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            run.to_row(),
        )

    @_serialized
    def update_run(self, run: RunData) -> None:
        self._conn.execute(
            """UPDATE runs SET status=?, ended_at=?, total_cost=?, total_tokens_in=?,
               total_tokens_out=?, degraded_count=?, metadata=? WHERE run_id=?""",
            (
                run.status,
                run.ended_at.isoformat() if run.ended_at else None,
                float(run.total_cost),
                run.total_tokens_in,
                run.total_tokens_out,
                run.degraded_count,
                __import__("json").dumps(run.metadata),
                run.run_id,
            ),
        )

    @_serialized
    def insert_span(self, span: SpanData) -> None:
        self._conn.execute(
            """INSERT INTO spans (span_id, run_id, parent_span_id, span_type, name,
               started_at, ended_at, duration_ms, model, model_requested, provider,
               tokens_in, tokens_out, cost, degraded, tool_name, tool_status, tool_error,
               context_tokens, context_health, eval_scores, safety_flags, status,
               error_message, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            span.to_row(),
        )

    @_serialized
    def insert_budget_event(
        self,
        agent_name: str,
        event_type: str,
        amount: float,
        balance_after: float,
        run_id: str | None = None,
        span_id: str | None = None,
        details: str | None = None,
    ) -> None:
        import uuid
        from datetime import datetime, timezone

        self._conn.execute(
            """INSERT INTO budget_ledger (id, timestamp, agent_name, event_type,
               amount, balance_after, run_id, span_id, details) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                agent_name,
                event_type,
                amount,
                balance_after,
                run_id,
                span_id,
                details,
            ),
        )

    @_serialized
    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as dicts."""
        if params:
            result = self._conn.execute(sql, params)
        else:
            result = self._conn.execute(sql)
        if result.description is None:
            return []
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    @_serialized
    def find_run(self, run_id_prefix: str) -> dict[str, Any] | None:
        """Find a run by ID prefix (safe from SQL injection)."""
        rows = self.query("SELECT * FROM runs WHERE run_id LIKE ? LIMIT 1", [run_id_prefix + "%"])
        return rows[0] if rows else None

    @_serialized
    def get_run_spans(self, run_id: str) -> list[dict[str, Any]]:
        """Get all spans for a run (safe from SQL injection)."""
        return self.query("SELECT * FROM spans WHERE run_id = ? ORDER BY started_at ASC", [run_id])

    @contextmanager
    def transaction(self):
        """Single-writer atomic transaction shared by all runtime modules."""
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                yield
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock, _OPEN_LOCK:
            if not self._closed:
                self._conn.close()
                self._closed = True
                _OPEN.pop(str(self._path), None)

    @_serialized
    def cleanup(self, retention_days: int = 30) -> int:
        """Delete data older than retention_days. Returns count of deleted spans."""
        from datetime import datetime, timedelta, timezone

        if retention_days < 0:
            raise ValueError("retention_days must be nonnegative")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        count = self.query("SELECT COUNT(*) AS n FROM spans WHERE started_at < ?", [cutoff])[0]["n"]
        self.query("DELETE FROM spans WHERE started_at < ?", [cutoff])
        # Budget requests and outcomes are not deleted by trace retention.
        return count
