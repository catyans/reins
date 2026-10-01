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
    mode: str = "observe"
    policy_version: str = "baseline"
    task_models: dict[str, list[str]] = field(default_factory=dict)
    prices: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    token_counter: Any = None
    control_url: str = "http://127.0.0.1:8795"
    control_token_file: str | None = None
    dashboard: bool = False
    dashboard_port: int = 8765
    inactivity_seconds: float = 60

    def validate(self) -> None:
        import math

        if type(self.dashboard) is not bool:
            raise ValueError("dashboard must be boolean")
        if type(self.dashboard_port) is not int or not 0 <= self.dashboard_port <= 65535:
            raise ValueError("dashboard_port must be between 0 and 65535")
        if (
            not isinstance(self.inactivity_seconds, (int, float))
            or not math.isfinite(self.inactivity_seconds)
            or self.inactivity_seconds <= 0
        ):
            raise ValueError("inactivity_seconds must be positive and finite")
        if self.mode not in {"observe", "enforce"}:
            raise ValueError("mode must be observe or enforce")
        for cfg in [self.budget, *self.budget.agents.values()]:
            if cfg.on_exceed not in {"alert", "reject", "pause", "degrade"}:
                raise ValueError("Invalid on_exceed strategy")
            for key in ("per_run", "daily", "monthly"):
                value = getattr(cfg, key, None)
                if value is not None and (not value.is_finite() or value < 0):
                    raise ValueError("Budgets must be finite and nonnegative")
        for task, models in self.task_models.items():
            if not isinstance(task, str) or not isinstance(models, list) or not models:
                raise ValueError("task_models maps task types to ordered provider/model lists")
            if len(set(models)) != len(models) or any("/" not in m for m in models):
                raise ValueError("Models must be unique provider/model identifiers")
        for models in self.prices.values():
            for price in models.values():
                if len(price) != 2 or any(
                    not Decimal(str(x)).is_finite() or Decimal(str(x)) < 0 for x in price
                ):
                    raise ValueError("prices requires nonnegative input/output USD per million")

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

        config.mode = raw.get("mode", "observe")
        config.policy_version = raw.get("policy_version", "baseline")
        config.task_models = raw.get("task_models", {})
        config.prices = raw.get("prices", {})
        for key in (
            "dashboard",
            "dashboard_port",
            "inactivity_seconds",
            "control_url",
            "control_token_file",
        ):
            if key in raw:
                setattr(config, key, raw[key])
        config.validate()
        return config


def _parse_money(value: Any) -> Decimal | None:
    """Parse money strings like '$5.00', '$5/day', '5.00'."""
    if value is None:
        return None
    s = re.sub(r"^\$", "", str(value).strip())
    s = re.sub(r"/(day|month|run|hour)$", "", s, flags=re.IGNORECASE)
    try:
        result = Decimal(s.strip())
    except Exception as exc:
        raise ValueError(f"Invalid budget: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Budget must be finite and nonnegative")
    return result
