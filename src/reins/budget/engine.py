"""Durable single-writer task budgets. Unknown charges remain reserved."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from reins.core.pricing import UnknownPriceError, get_price


class BudgetExceededError(Exception):
    def __init__(self, agent, balance):
        self.agent, self.balance = agent, balance
        super().__init__(f"Budget exceeded for {agent!r} (available ${balance})")


class BudgetPausedError(BudgetExceededError):
    """Caller must explicitly arrange resume; no automatic task suspension."""


class CostBoundError(ValueError):
    """Strict admission needs verified input count and an explicit output bound."""


class BudgetEngine:
    def __init__(self, event_bus, storage, config):
        config.validate()
        self._event_bus, self._storage, self._config = event_bus, storage, config

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def _scopes(self, span):
        now = self._now()
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        agent = span.metadata.get("agent_name", "default")
        scopes = {}
        for label, cfg in [
            ("global", self._config.budget),
            (f"agent:{agent}", self._config.budget.agents.get(agent)),
        ]:
            if cfg is None:
                continue
            for period, stamp in [("daily", day), ("monthly", month)]:
                limit = getattr(cfg, period, None)
                if limit is not None:
                    scopes[json.dumps([label, period, stamp])] = limit
        # Each ancestor task cap and the current cap constrain the same request.
        runs = list(span.metadata.get("budget_ancestors", []))
        cfg = self._config.budget.agents.get(agent)
        limits = [
            x
            for x in [getattr(cfg, "per_run", None), span.metadata.get("budget_limit")]
            if x is not None
        ]
        if limits:
            if not span.run_id:
                raise ValueError("Per-run budgets require a traced run_id")
            runs.append((span.run_id, str(min(Decimal(str(x)) for x in limits))))
        for run, limit in runs:
            scopes[json.dumps(["run", run])] = Decimal(str(limit))
        return scopes

    def _available(self, scopes):
        balances = []
        for scope, limit in scopes.items():
            rows = self._storage.query(
                """SELECT COALESCE(SUM(COALESCE(r.actual, r.reserved)),0) AS used
                FROM budget_requests r JOIN budget_allocations a USING(span_id)
                WHERE a.scope=?""",
                [scope],
            )
            balances.append(limit - Decimal(str(rows[0]["used"])))
        return min(balances) if balances else Decimal("Infinity")

    def _estimate_cost(self, span, model=None):
        return get_price(
            span.provider,
            model or span.model,
            span.estimated_input_tokens,
            span.estimated_max_output_tokens,
            self._config.prices,
        )

    def on_span_start(self, span):
        if span.span_type != "llm" or span.metadata.get("adapter_observation"):
            return span
        mode = span.metadata.get("mode", self._config.mode)
        if mode not in {"observe", "enforce"}:
            raise ValueError("Invalid mode")
        span.metadata["mode"] = mode
        agent = span.metadata.get("agent_name", "default")
        cfg = self._config.budget.agents.get(agent)
        strategy = span.metadata.get("on_exceed") or getattr(
            cfg, "on_exceed", self._config.budget.on_exceed
        )
        if strategy not in {"alert", "pause", "reject", "degrade"}:
            raise ValueError("Invalid on_exceed strategy")
        scopes = self._scopes(span)
        models = self._config.task_models.get(span.metadata.get("task_type", ""), [])
        identity = f"{span.provider}/{span.model}"
        counter = getattr(span, "_bound_counter", None)
        if mode == "enforce":
            if span.model not in self._config.prices.get(span.provider, {}):
                raise UnknownPriceError("Enforce requires explicitly pinned prices for this model")
            if not span.metadata.get("input_bound_verified") or not span.metadata.get(
                "output_bound_verified"
            ):
                raise CostBoundError(
                    "Enforce requires configure(token_counter=...) "
                    "and explicit max_tokens/max_completion_tokens"
                )
            if span.metadata.get("observe_only"):
                raise CostBoundError(
                    "This adapter only records observations; use the SDK execution path"
                )
            if models and identity not in models:
                raise CostBoundError(f"{identity} is not approved for this task type")
        with self._storage.transaction():
            if self._storage.query(
                "SELECT span_id FROM budget_requests WHERE span_id=?", [span.span_id]
            ):
                raise ValueError("A request id cannot be reused for another provider call")
            # Unknown in-flight prices block strict spending until reconciled.
            if mode == "enforce" and self._storage.query(
                "SELECT span_id FROM budget_requests "
                "WHERE (reserved IS NULL AND actual IS NULL) OR status='bound_exceeded' LIMIT 1"
            ):
                raise CostBoundError("Unpriced prior requests require reconciliation")
            try:
                estimated = self._estimate_cost(span)
            except UnknownPriceError:
                if mode == "enforce":
                    raise
                estimated = None
            available = self._available(scopes)
            decision = (
                "allow" if estimated is not None and available >= estimated else "would_reject"
            )
            if estimated is not None and available < estimated and strategy == "degrade":
                # Only explicit, task-approved, same-provider candidates are eligible.
                candidates = models[models.index(identity) + 1 :] if identity in models else []
                for candidate in candidates:
                    provider, model = candidate.split("/", 1)
                    if provider != span.provider:
                        continue
                    if mode == "enforce" and model not in self._config.prices.get(provider, {}):
                        continue
                    # Count against the candidate tokenizer/format as well.
                    old_input = span.estimated_input_tokens
                    if counter:
                        bound = counter(model)
                        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
                            raise CostBoundError("token_counter must return a nonnegative integer")
                        span.estimated_input_tokens = bound
                    try:
                        cost = self._estimate_cost(span, model)
                    except UnknownPriceError:
                        span.estimated_input_tokens = old_input
                        continue
                    if cost <= available and cost < estimated:
                        span.metadata["recommended_model"] = model
                        decision = "would_degrade"
                        if mode == "enforce":
                            span.model, span.degraded, estimated = model, True, cost
                            decision = "degrade"
                        else:
                            span.estimated_input_tokens = old_input
                        break
                    span.estimated_input_tokens = old_input
            if mode == "enforce" and (estimated is None or estimated > available):
                error = BudgetPausedError if strategy == "pause" else BudgetExceededError
                # Strict mode never interprets alert as permission to overspend.
                raise error(agent, available)
            span.metadata["budget_decision"] = decision
            span.metadata["cost_status"] = "pending"
            self._storage.query(
                "INSERT INTO budget_requests VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    span.span_id,
                    span.run_id,
                    agent,
                    estimated,
                    None,
                    "pending",
                    self._now().isoformat(),
                    span.model,
                    mode,
                ],
            )
            for scope in scopes:
                self._storage.query(
                    "INSERT INTO budget_allocations VALUES (?,?)", [span.span_id, scope]
                )
        return span

    def on_span_end(self, span):
        # Only provider-confirmed usage or explicit reconciliation releases funds.
        known = span.metadata.get("cost_status") == "known"
        with self._storage.transaction():
            rows = self._storage.query(
                "SELECT * FROM budget_requests WHERE span_id=?", [span.span_id]
            )
            if not rows or rows[0]["actual"] is not None:
                return
            if not known:
                span.metadata["cost_status"] = "pending"
                return
            actual = span.cost
            row = rows[0]
            overrun = row["reserved"] is not None and actual > row["reserved"]
            status = "bound_exceeded" if overrun else "settled"
            self._storage.query(
                "UPDATE budget_requests SET actual=?, status=? WHERE span_id=?",
                [actual, status, span.span_id],
            )
            if overrun:
                span.metadata["budget_bound_exceeded"] = True
        if overrun:
            self._event_bus.emit("budget.bound_exceeded", span_id=span.span_id)

    def reconcile(self, span_id, actual_cost):
        """Explicit caller confirmation, including zero for confirmed non-billed calls."""
        cost = Decimal(str(actual_cost))
        if not cost.is_finite() or cost < 0:
            raise ValueError("actual_cost must be finite and nonnegative")
        with self._storage.transaction():
            rows = self._storage.query(
                "SELECT actual FROM budget_requests WHERE span_id=?", [span_id]
            )
            if not rows:
                raise KeyError(span_id)
            if rows[0]["actual"] is not None:
                if rows[0]["actual"] == cost:
                    self._storage.query(
                        "UPDATE budget_requests SET status='reconciled' WHERE span_id=?", [span_id]
                    )
                    return
                raise ValueError("Already settled with a different charge")
            self._storage.query(
                "UPDATE budget_requests SET actual=?, status='reconciled' WHERE span_id=?",
                [cost, span_id],
            )
