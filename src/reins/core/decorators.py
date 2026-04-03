"""Public API: @trace(), wrap(), configure()."""

from __future__ import annotations

import asyncio
import functools
import logging
from decimal import Decimal
from typing import Any, Callable, TypeVar, overload

from reins.core.config import ReinsConfig
from reins.core.context import get_current_run, set_current_run
from reins.core.models import RunData

logger = logging.getLogger("reins")

F = TypeVar("F", bound=Callable[..., Any])

# Global runtime state (lazily initialized)
_runtime: _Runtime | None = None


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
        self.instrumentor = Instrumentor(self.event_bus, self.storage, self.modules)
        self.instrumentor.instrument()

    def shutdown(self) -> None:
        self.instrumentor.uninstrument()
        for module in self.modules:
            module.shutdown()
        self.storage.close()


def _get_runtime() -> _Runtime:
    global _runtime
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
    on_exceed: str = "alert",
    agent_name: str | None = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def trace(
    fn: F | None = None,
    *,
    budget: str | float | None = None,
    on_exceed: str = "alert",
    agent_name: str | None = None,
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

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kw: Any) -> Any:
            runtime = _get_runtime()
            run = RunData(agent_name=name, budget_limit=budget_limit)
            run.metadata["on_exceed"] = on_exceed

            token = set_current_run(run)
            try:
                runtime.storage.insert_run(run)
                runtime.event_bus.emit("core.run_start", run=run)

                result = await func(*args, **kw)

                run.complete("completed")
            except Exception as e:
                run.complete("failed")
                raise
            finally:
                runtime.event_bus.emit("core.run_end", run=run)
                try:
                    runtime.storage.update_run(run)
                except Exception:
                    logger.debug("Failed to update run", exc_info=True)
                set_current_run(None)

            return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kw: Any) -> Any:
            runtime = _get_runtime()
            run = RunData(agent_name=name, budget_limit=budget_limit)
            run.metadata["on_exceed"] = on_exceed

            token = set_current_run(run)
            try:
                runtime.storage.insert_run(run)
                runtime.event_bus.emit("core.run_start", run=run)

                result = func(*args, **kw)

                run.complete("completed")
            except Exception as e:
                run.complete("failed")
                raise
            finally:
                runtime.event_bus.emit("core.run_end", run=run)
                try:
                    runtime.storage.update_run(run)
                except Exception:
                    logger.debug("Failed to update run", exc_info=True)
                set_current_run(None)

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
    if _runtime:
        _runtime.shutdown()
    config = ReinsConfig.load()
    # Apply overrides
    if "storage_path" in kwargs:
        config.storage_path = kwargs["storage_path"]
    _runtime = _Runtime(config)


def shutdown() -> None:
    """Shutdown the Reins runtime. For testing/cleanup."""
    global _runtime
    if _runtime:
        _runtime.shutdown()
        _runtime = None
