"""Budget engine: atomic reservation, degradation, circuit breaker."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from reins.budget.circuit_breaker import CircuitBreaker
from reins.budget.degradation import get_cheaper_model
from reins.core.config import ReinsConfig
from reins.core.events import EventBus, Events
from reins.core.models import SpanData
from reins.core.pricing import get_price
from reins.core.storage import Storage

logger = logging.getLogger("reins.budget")


class BudgetExceededError(Exception):
    def __init__(self, agent: str, balance: Decimal):
        self.agent = agent
        self.balance = balance
        super().__init__(f"Budget exceeded for agent '{agent}' (balance: ${balance})")


class BudgetPausedError(Exception):
    def __init__(self, agent: str, balance: Decimal):
        self.agent = agent
        self.balance = balance
        super().__init__(f"Budget paused for agent '{agent}' (balance: ${balance})")


class BudgetEngine:
    """Core budget engine with atomic reservation pattern."""

    def __init__(self, event_bus: EventBus, storage: Storage, config: ReinsConfig):
        self._event_bus = event_bus
        self._storage = storage
        self._config = config
        self._lock = threading.Lock()
        self._balances: dict[str, Decimal] = {}
        self._reservations: dict[str, Decimal] = {}  # span_id -> reserved amount
        self._circuit_breaker = CircuitBreaker()

        # Load persisted balances from DuckDB
        self._ensure_balance_table()
        self._load_balances()

        # Listen for context rot to reduce budgets
        event_bus.on(Events.CONTEXT_ROT, self._on_context_rot)

    def _ensure_balance_table(self) -> None:
        """Create budget_balances table if it doesn't exist."""
        try:
            self._storage.query("""
                CREATE TABLE IF NOT EXISTS budget_balances (
                    agent_name VARCHAR PRIMARY KEY,
                    balance DOUBLE NOT NULL,
                    period_type VARCHAR NOT NULL,
                    period_start VARCHAR NOT NULL,
                    updated_at VARCHAR NOT NULL
                )
            """)
        except Exception:
            logger.debug("Failed to create budget_balances table", exc_info=True)

    def _load_balances(self) -> None:
        """Load persisted balances, resetting if the period has expired."""
        try:
            rows = self._storage.query("SELECT * FROM budget_balances")
        except Exception:
            return

        now = datetime.now(timezone.utc)

        for row in rows:
            agent = row["agent_name"]
            balance = Decimal(str(row["balance"]))
            period_type = row["period_type"]
            period_start = row["period_start"]

            if self._period_expired(period_type, period_start, now):
                # Period expired — reset balance (will be re-initialized on next call)
                logger.info("Budget period expired for %s, resetting", agent)
                continue

            self._balances[agent] = balance

    def _persist_balance(self, agent: str, balance: Decimal, period_type: str = "daily") -> None:
        """Persist balance to DuckDB for crash recovery."""
        now = datetime.now(timezone.utc)
        try:
            # Upsert
            self._storage.query(f"""
                DELETE FROM budget_balances WHERE agent_name = '{agent}'
            """)
            self._storage.query(f"""
                INSERT INTO budget_balances (agent_name, balance, period_type, period_start, updated_at)
                VALUES ('{agent}', {float(balance)}, '{period_type}', '{now.date().isoformat()}', '{now.isoformat()}')
            """)
        except Exception:
            logger.debug("Failed to persist balance for %s", agent, exc_info=True)

    @staticmethod
    def _period_expired(period_type: str, period_start: str, now: datetime) -> bool:
        """Check if a budget period has expired."""
        try:
            start_date = datetime.fromisoformat(period_start).date() if "T" in period_start else datetime.strptime(period_start, "%Y-%m-%d").date()
        except Exception:
            return True

        today = now.date()
        if period_type == "daily":
            return today > start_date
        elif period_type == "monthly":
            return today.year > start_date.year or today.month > start_date.month
        return False

    def on_span_start(self, span: SpanData) -> SpanData:
        """Pre-call: reserve budget, degrade if needed."""
        agent = span.metadata.get("agent_name", "default")
        on_exceed = span.metadata.get("on_exceed", "alert")

        # Circuit breaker check
        if self._circuit_breaker.check(agent):
            self._event_bus.emit(Events.BUDGET_CIRCUIT_BREAK, agent=agent)
            raise BudgetExceededError(agent, self._get_balance(agent))

        # Get budget config
        budget_config = self._config.get_agent_budget(agent)
        if not budget_config:
            # Check run-level budget from decorator
            from reins.core.context import get_current_run

            run = get_current_run()
            if run and run.budget_limit:
                limit = run.budget_limit
                on_exceed_run = run.metadata.get("on_exceed", "alert")
            else:
                return span  # No budget configured
        else:
            limit = budget_config.per_run or budget_config.daily or Decimal("999999")
            on_exceed = budget_config.on_exceed

        # Estimate cost
        estimated = self._estimate_cost(span)

        with self._lock:
            balance = self._get_balance(agent, limit)

            if balance >= estimated:
                # Normal reservation
                self._balances[agent] = balance - estimated
                self._reservations[span.span_id] = estimated
                self._log_event(agent, "reserve", estimated, self._balances[agent], span)
                return span

            # Budget pressure — check threshold
            if balance / limit < Decimal("0.15"):
                self._event_bus.emit(
                    Events.BUDGET_THRESHOLD, agent=agent, usage=float(1 - balance / limit)
                )

            # Handle exceed
            return self._handle_exceed(span, agent, on_exceed, balance, estimated, limit)

    def on_span_end(self, span: SpanData) -> None:
        """Post-call: reconcile actual vs estimated cost."""
        reserved = self._reservations.pop(span.span_id, None)
        if reserved is None:
            return

        actual = span.cost
        delta = reserved - actual
        agent = span.metadata.get("agent_name", "default")

        with self._lock:
            self._balances[agent] = self._balances.get(agent, Decimal("0")) + delta
            new_balance = self._balances[agent]

        self._log_event(agent, "commit", float(actual), float(new_balance), span)
        # Persist updated balance
        self._persist_balance(agent, new_balance)

    def _handle_exceed(
        self,
        span: SpanData,
        agent: str,
        strategy: str,
        balance: Decimal,
        estimated: Decimal,
        limit: Decimal,
    ) -> SpanData:
        """Handle budget exceeded."""
        if strategy == "degrade":
            cheaper = get_cheaper_model(span.provider, span.model)
            if cheaper:
                span.model = cheaper
                span.degraded = True
                new_est = self._estimate_cost(span)
                self._balances[agent] = balance - new_est
                self._reservations[span.span_id] = new_est

                self._event_bus.emit(
                    Events.BUDGET_DEGRADED,
                    agent=agent,
                    original=span.model_requested,
                    degraded=cheaper,
                )
                logger.info(
                    "Degraded %s: %s → %s (balance: $%.4f)",
                    agent, span.model_requested, cheaper, self._balances[agent],
                )
                self._log_event(agent, "degrade", float(new_est), float(self._balances[agent]), span)
                return span

            # No cheaper model, fall through to reject
            strategy = "reject"

        if strategy == "alert":
            self._event_bus.emit(Events.BUDGET_EXCEEDED, agent=agent, balance=float(balance))
            logger.warning("Budget exceeded for %s (balance: $%.4f), continuing", agent, balance)
            self._balances[agent] = balance - estimated
            self._reservations[span.span_id] = estimated
            return span

        if strategy == "pause":
            self._event_bus.emit(Events.BUDGET_EXCEEDED, agent=agent, balance=float(balance))
            raise BudgetPausedError(agent, balance)

        # reject
        self._event_bus.emit(Events.BUDGET_EXCEEDED, agent=agent, balance=float(balance))
        raise BudgetExceededError(agent, balance)

    def _estimate_cost(self, span: SpanData) -> Decimal:
        """Estimate cost before the call using actual prompt token estimates.

        Uses estimated_input_tokens (from prompt char count) and
        estimated_max_output_tokens (from max_tokens param) for a much
        more accurate pre-call estimate than a hardcoded number.
        """
        estimated_in = span.estimated_input_tokens or 500
        # Use max_tokens as upper bound, but assume ~50% utilization
        estimated_out = max((span.estimated_max_output_tokens or 1024) // 2, 100)
        return get_price(span.provider, span.model, estimated_in, estimated_out)

    def _get_balance(self, agent: str, default: Decimal | None = None) -> Decimal:
        if agent not in self._balances and default is not None:
            self._balances[agent] = default
        return self._balances.get(agent, Decimal("999999"))

    def _log_event(
        self, agent: str, event_type: str, amount: Any, balance: Any, span: SpanData
    ) -> None:
        try:
            self._storage.insert_budget_event(
                agent_name=agent,
                event_type=event_type,
                amount=float(amount),
                balance_after=float(balance),
                run_id=span.run_id,
                span_id=span.span_id,
            )
        except Exception:
            logger.debug("Failed to log budget event", exc_info=True)

    def _on_context_rot(self, **kwargs: Any) -> None:
        """When context rot is detected, reduce remaining budget."""
        agent = kwargs.get("agent", "default")
        with self._lock:
            if agent in self._balances:
                # Reduce remaining budget by 30%
                self._balances[agent] *= Decimal("0.7")
                logger.info("Reduced budget for %s due to context rot", agent)
