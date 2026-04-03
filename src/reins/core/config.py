"""Configuration loading from reins.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AgentBudgetConfig:
    """Budget configuration for a single agent."""

    per_run: Decimal | None = None
    daily: Decimal | None = None
    monthly: Decimal | None = None
    on_exceed: str = "alert"  # degrade | pause | alert | reject

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentBudgetConfig:
        return cls(
            per_run=_parse_money(d.get("per_run")),
            daily=_parse_money(d.get("daily")),
            monthly=_parse_money(d.get("monthly")),
            on_exceed=d.get("on_exceed", "alert"),
        )


@dataclass
class BudgetConfig:
    """Top-level budget configuration."""

    daily: Decimal | None = None
    monthly: Decimal | None = None
    on_exceed: str = "alert"
    agents: dict[str, AgentBudgetConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BudgetConfig:
        agents = {}
        for name, agent_cfg in d.get("agents", {}).items():
            agents[name] = AgentBudgetConfig.from_dict(agent_cfg)
        return cls(
            daily=_parse_money(d.get("daily")),
            monthly=_parse_money(d.get("monthly")),
            on_exceed=d.get("on_exceed", "alert"),
            agents=agents,
        )


@dataclass
class ModuleConfig:
    """Configuration for a single module."""

    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReinsConfig:
    """Top-level Reins configuration."""

    budget: BudgetConfig = field(default_factory=BudgetConfig)
    modules: dict[str, ModuleConfig] = field(default_factory=dict)
    storage: str = "duckdb"
    storage_path: str | None = None
    retention_days: int = 30

    def get_agent_budget(self, agent_name: str) -> AgentBudgetConfig | None:
        cfg = self.budget.agents.get(agent_name)
        if cfg:
            return cfg
        # Fallback: create config from top-level budget
        if self.budget.daily or self.budget.monthly:
            return AgentBudgetConfig(
                daily=self.budget.daily,
                monthly=self.budget.monthly,
                on_exceed=self.budget.on_exceed,
            )
        return None

    def is_module_enabled(self, name: str) -> bool:
        mc = self.modules.get(name)
        if mc is None:
            return True  # Enabled by default if installed
        return mc.enabled

    @classmethod
    def load(cls, path: Path | str | None = None) -> ReinsConfig:
        """Load config from reins.yaml. Falls back to defaults if not found."""
        if path is None:
            # Search in CWD and home
            candidates = [
                Path.cwd() / "reins.yaml",
                Path.cwd() / "reins.yml",
                Path.home() / ".reins" / "config.yaml",
            ]
            for candidate in candidates:
                if candidate.exists():
                    path = candidate
                    break
        if path is None:
            return cls()  # Default config

        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> ReinsConfig:
        config = cls()

        if "budgets" in raw:
            config.budget = BudgetConfig.from_dict(raw["budgets"])
        elif "budget" in raw:
            config.budget = BudgetConfig.from_dict(raw["budget"])

        if "modules" in raw:
            for name, mod_cfg in raw["modules"].items():
                if isinstance(mod_cfg, dict):
                    config.modules[name] = ModuleConfig(
                        enabled=mod_cfg.get("enabled", True),
                        settings=mod_cfg,
                    )

        config.storage = raw.get("storage", "duckdb")
        config.storage_path = raw.get("storage_path")
        config.retention_days = raw.get("retention_days", 30)

        return config


def _parse_money(value: Any) -> Decimal | None:
    """Parse money strings like '$5.00', '$5/day', '5.00'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    s = str(value).strip()
    # Remove $ prefix and /period suffix
    s = re.sub(r"^\$", "", s)
    s = re.sub(r"/(day|month|run|hour)$", "", s, flags=re.IGNORECASE)
    s = s.strip()
    try:
        return Decimal(s)
    except Exception:
        return None
