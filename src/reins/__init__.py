"""Reins: Take control of your AI agents."""

__version__ = "0.2.0"

from reins.core.decorators import configure, wrap
from reins.core.decorators import trace as _trace

from . import trace as _trace_module

__all__ = [
    "workflow",
    "bind_context",
    "export_context",
    "report_progress",
    "trace",
    "step",
    "wrap",
    "configure",
    "__version__",
    "record_outcome",
    "record_external_cost",
    "reconcile_cost",
    "record_retry",
    "BatchKey",
    "DeadlineBatcher",
    "Delivery",
    "CheckpointStore",
    "UnsettledAttempt",
    "PolicyBundle",
    "learn_bundle",
    "assess_holdout",
]

from reins.checkpoints import CheckpointStore, UnsettledAttempt
from reins.control import bind_context, export_context, report_progress, workflow
from reins.core.monitor import step
from reins.core.outcomes import reconcile_cost, record_external_cost, record_outcome, record_retry
from reins.policies import PolicyBundle, assess_holdout, learn_bundle
from reins.scheduling import BatchKey, DeadlineBatcher, Delivery

# Preload the trace subpackage so later imports cannot replace the decorator.
trace = _trace
del _trace_module
