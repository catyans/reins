"""Public API: @trace(), wrap(), configure()."""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
from decimal import Decimal
from typing import Any, Callable, TypeVar, overload

from reins.core.config import ReinsConfig
from reins.core.context import get_current_run, reset_current_run, set_current_run
from reins.core.models import RunData

logger = logging.getLogger("reins")

F = TypeVar("F", bound=Callable[..., Any])

# Global runtime state (lazily initialized)
_runtime: _Runtime | None = None
_runtime_lock = threading.RLock()


class _Runtime:
    """Singleton holding the initialized Reins runtime."""

    def __init__(self, config: ReinsConfig | None = None):
        from reins.core.events import EventBus
        from reins.core.instrumentor import Instrumentor
        from reins.core.loader import load_modules
        from reins.core.storage import Storage

        self.config = config or ReinsConfig.load()
        self.event_bus = EventBus()
        self.storage = Storage(self.config.storage_path)
        self.modules = load_modules(self.event_bus, self.storage, self.config)
        if not any(m.name == "budget" for m in self.modules):
            self.storage.close()
            raise RuntimeError("Budget module failed to load; refusing unmetered runtime")
        self.instrumentor = Instrumentor(self.event_bus, self.storage, self.modules, self.config)
        from reins.core.monitor import Monitor

        self.monitor = Monitor(self.storage, self.event_bus, self.config)
        self.dashboard_server = None
        try:
            if self.config.dashboard:
                from reins.dashboard.server import DashboardServer

                self.dashboard_server = DashboardServer(self)
            self.instrumentor.instrument()
        except BaseException:
            self.monitor.close()
            self.storage.close()
            raise

    def shutdown(self) -> None:
        if self.dashboard_server:
            self.dashboard_server.close()
        self.monitor.close()
        self.instrumentor.uninstrument()
        for module in self.modules:
            module.shutdown()
        self.storage.close()


def _get_runtime() -> _Runtime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = _Runtime()
        return _runtime


def _parse_budget(budget: str | float | None) -> Decimal | None:
    if budget is None:
        return None
    if isinstance(budget, (int, float)):
        return Decimal(str(budget))
    from reins.core.config import _parse_money

    return _parse_money(budget)


@overload
def trace(fn: F) -> F: ...
@overload
def trace(
    *,
    budget: str | float | None = None,
    on_exceed: str | None = None,
    agent_name: str | None = None,
    task_type: str = "default",
    policy_version: str | None = None,
    mode: str | None = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def trace(
    fn: F | None = None,
    *,
    budget: str | float | None = None,
    on_exceed: str | None = None,
    agent_name: str | None = None,
    task_type: str = "default",
    policy_version: str | None = None,
    mode: str | None = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Decorator to trace an agent function.

    Usage:
        @trace
        async def my_agent(task): ...

        @trace(budget="$0.50", on_exceed="degrade")
        async def my_agent(task): ...
    """

    def decorator(func: F) -> F:
        name = agent_name or func.__name__
        budget_limit = _parse_budget(budget)
        if kwargs:
            raise TypeError(f"Unknown trace options: {sorted(kwargs)}")
        if mode is not None and mode not in {"observe", "enforce"}:
            raise ValueError("mode must be observe or enforce")
        if on_exceed is not None and on_exceed not in {"alert", "reject", "pause", "degrade"}:
            raise ValueError("Invalid on_exceed strategy")

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kw: Any) -> Any:
            runtime = _get_runtime()
            run = RunData(agent_name=name, budget_limit=budget_limit)
            parent = get_current_run()
            if parent and parent.metadata.get("mode") == "enforce" and mode == "observe":
                raise ValueError("An enforced parent cannot be bypassed by an observed child")
            ancestors = [] if parent is None else list(parent.metadata.get("budget_ancestors", []))
            if parent:
                parent_cfg = runtime.config.budget.agents.get(parent.agent_name)
                caps = [
                    x
                    for x in [parent.budget_limit, getattr(parent_cfg, "per_run", None)]
                    if x is not None
                ]
                if caps:
                    ancestors.append((parent.run_id, str(min(caps))))
            run.metadata.update(
                on_exceed=on_exceed,
                task_type=task_type,
                policy_version=policy_version or runtime.config.policy_version,
                mode=mode or (parent.metadata["mode"] if parent else runtime.config.mode),
                budget_ancestors=ancestors,
                parent_run_id=parent.run_id if parent else None,
                root_run_id=parent.metadata.get("root_run_id", parent.run_id)
                if parent
                else run.run_id,
            )

            token = set_current_run(run)
            try:
                runtime.storage.insert_run(run)
                runtime.event_bus.emit("core.run_start", run=run)

                result = await func(*args, **kw)

                run.complete("completed")
            except BaseException as exc:
                run.metadata["failure_reason"] = type(exc).__name__
                run.complete("failed")
                raise
            finally:
                runtime.event_bus.emit("core.run_end", run=run)
                try:
                    runtime.storage.update_run(run)
                except Exception:
                    logger.debug("Failed to update run", exc_info=True)
                reset_current_run(token)

            return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kw: Any) -> Any:
            runtime = _get_runtime()
            run = RunData(agent_name=name, budget_limit=budget_limit)
            parent = get_current_run()
            if parent and parent.metadata.get("mode") == "enforce" and mode == "observe":
                raise ValueError("An enforced parent cannot be bypassed by an observed child")
            ancestors = [] if parent is None else list(parent.metadata.get("budget_ancestors", []))
            if parent:
                parent_cfg = runtime.config.budget.agents.get(parent.agent_name)
                caps = [
                    x
                    for x in [parent.budget_limit, getattr(parent_cfg, "per_run", None)]
                    if x is not None
                ]
                if caps:
                    ancestors.append((parent.run_id, str(min(caps))))
            run.metadata.update(
                on_exceed=on_exceed,
                task_type=task_type,
                policy_version=policy_version or runtime.config.policy_version,
                mode=mode or (parent.metadata["mode"] if parent else runtime.config.mode),
                budget_ancestors=ancestors,
                parent_run_id=parent.run_id if parent else None,
                root_run_id=parent.metadata.get("root_run_id", parent.run_id)
                if parent
                else run.run_id,
            )

            token = set_current_run(run)
            try:
                runtime.storage.insert_run(run)
                runtime.event_bus.emit("core.run_start", run=run)

                result = func(*args, **kw)

                run.complete("completed")
            except BaseException as exc:
                run.metadata["failure_reason"] = type(exc).__name__
                run.complete("failed")
                raise
            finally:
                runtime.event_bus.emit("core.run_end", run=run)
                try:
                    runtime.storage.update_run(run)
                except Exception:
                    logger.debug("Failed to update run", exc_info=True)
                reset_current_run(token)

            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator  # type: ignore[return-value]


def wrap(client: Any, budget: str | float | None = None, **kwargs: Any) -> Any:
    """Wrap an LLM client to auto-trace all calls.

    Usage:
        client = wrap(anthropic.Anthropic(), budget="$0.50")
    """
    if budget is not None or kwargs:
        raise ValueError("wrap does not define a task budget; use @trace(budget=...)")
    # Ensure runtime is initialized (instruments the SDK globally)
    _get_runtime()
    # The client is already instrumented via monkey-patch, so just return it.
    # Budget is handled via the current run context.
    return client


def configure(**kwargs: Any) -> None:
    """Configure the Reins runtime.

    Usage:
        reins.configure(budget="$10/day", storage_path="/tmp/reins.db")
    """
    global _runtime
    allowed = {
        "storage_path",
        "config_path",
        "mode",
        "policy_version",
        "task_models",
        "prices",
        "token_counter",
        "dashboard",
        "dashboard_port",
        "inactivity_seconds",
    }
    if set(kwargs) - allowed:
        raise TypeError(f"Unknown configuration options: {sorted(set(kwargs) - allowed)}")
    config = ReinsConfig.load(kwargs.get("config_path"))
    for key, value in kwargs.items():
        if key != "config_path":
            setattr(config, key, value)
    config.validate()
    if _runtime:
        _runtime.shutdown()
        _runtime = None
    _runtime = _Runtime(config)


def shutdown() -> None:
    """Shutdown the Reins runtime. For testing/cleanup."""
    global _runtime
    if _runtime:
        _runtime.shutdown()
        _runtime = None
