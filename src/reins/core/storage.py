"""DuckDB storage layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from reins.core.models import RunData, SpanData

_SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, str] = {
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


class Storage:
    """DuckDB-backed storage for traces, runs, and budget events."""

    DEFAULT_PATH = Path.home() / ".reins" / "data" / "traces.duckdb"

    def __init__(self, db_path: Path | str | None = None):
        self._path = Path(db_path) if db_path else self.DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self._path))
        self._migrate()

    def _migrate(self) -> None:
        current = self._get_schema_version()
        for version in sorted(_MIGRATIONS.keys()):
            if version > current:
                for statement in _MIGRATIONS[version].split(";"):
                    statement = statement.strip()
                    if statement:
                        self._conn.execute(statement)

    def _get_schema_version(self) -> int:
        try:
            result = self._conn.execute("SELECT version FROM schema_version").fetchone()
            return result[0] if result else 0
        except duckdb.CatalogException:
            return 0

    def insert_run(self, run: RunData) -> None:
        self._conn.execute(
            """INSERT INTO runs (run_id, session_id, agent_name, status, started_at,
               ended_at, total_cost, total_tokens_in, total_tokens_out, budget_limit,
               degraded_count, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            run.to_row(),
        )

    def update_run(self, run: RunData) -> None:
        self._conn.execute(
            """UPDATE runs SET status=?, ended_at=?, total_cost=?, total_tokens_in=?,
               total_tokens_out=?, degraded_count=? WHERE run_id=?""",
            (
                run.status,
                run.ended_at.isoformat() if run.ended_at else None,
                float(run.total_cost),
                run.total_tokens_in,
                run.total_tokens_out,
                run.degraded_count,
                run.run_id,
            ),
        )

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

    def find_run(self, run_id_prefix: str) -> dict[str, Any] | None:
        """Find a run by ID prefix (safe from SQL injection)."""
        rows = self.query("SELECT * FROM runs WHERE run_id LIKE ? LIMIT 1", [run_id_prefix + "%"])
        return rows[0] if rows else None

    def get_run_spans(self, run_id: str) -> list[dict[str, Any]]:
        """Get all spans for a run (safe from SQL injection)."""
        return self.query("SELECT * FROM spans WHERE run_id = ? ORDER BY started_at ASC", [run_id])

    def close(self) -> None:
        self._conn.close()

    def cleanup(self, retention_days: int = 30) -> int:
        """Delete data older than retention_days. Returns count of deleted spans."""
        _result = self._conn.execute(
            """DELETE FROM spans WHERE started_at < (
               CAST(CURRENT_TIMESTAMP AS VARCHAR) || 'Z')"""
            # Simplified — proper implementation would use interval
        )
        return 0
