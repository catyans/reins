"""Module discovery and loading via entry_points."""

from __future__ import annotations

import importlib.metadata
import logging
from typing import TYPE_CHECKING

from reins.core.module import ReinsModule

if TYPE_CHECKING:
    from reins.core.config import ReinsConfig
    from reins.core.events import EventBus
    from reins.core.storage import Storage

logger = logging.getLogger("reins.loader")

ENTRY_POINT_GROUP = "reins.modules"


def discover_modules() -> list[type[ReinsModule]]:
    """Discover all installed Reins modules via entry_points."""
    modules: list[type[ReinsModule]] = []
    try:
        eps = importlib.metadata.entry_points()
        if isinstance(eps, dict):
            # Python 3.9-3.11
            group_eps = eps.get(ENTRY_POINT_GROUP, [])
        else:
            # Python 3.12+
            group_eps = eps.select(group=ENTRY_POINT_GROUP)

        for ep in group_eps:
            try:
                cls = ep.load()
                if isinstance(cls, type) and issubclass(cls, ReinsModule):
                    modules.append(cls)
            except Exception:
                logger.debug("Failed to load module %s", ep.name, exc_info=True)
    except Exception:
        logger.debug("Failed to discover modules", exc_info=True)
    return modules


def load_modules(
    event_bus: EventBus, storage: Storage, config: ReinsConfig
) -> list[ReinsModule]:
    """Load and initialize all enabled modules."""
    instances: list[ReinsModule] = []
    for cls in discover_modules():
        try:
            module = cls()
            if config.is_module_enabled(module.name):
                module.init(event_bus, storage, config)
                instances.append(module)
                logger.debug("Loaded module: %s", module.name)
        except Exception:
            logger.debug("Failed to init module %s", cls, exc_info=True)
    return instances
