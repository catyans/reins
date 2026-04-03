"""Run context management — propagates run_id through nested calls."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reins.core.models import RunData

_current_run: contextvars.ContextVar[RunData | None] = contextvars.ContextVar(
    "reins_current_run", default=None
)


def get_current_run() -> RunData | None:
    return _current_run.get()


def set_current_run(run: RunData | None) -> contextvars.Token:
    return _current_run.set(run)
