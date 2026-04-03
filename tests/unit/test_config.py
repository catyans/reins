"""Tests for configuration loading."""

import tempfile
from decimal import Decimal
from pathlib import Path

from reins.core.config import ReinsConfig, _parse_money


def test_parse_money_dollar_sign():
    assert _parse_money("$5.00") == Decimal("5.00")


def test_parse_money_with_period():
    assert _parse_money("$5/day") == Decimal("5")


def test_parse_money_number():
    assert _parse_money(5.0) == Decimal("5.0")


def test_parse_money_none():
    assert _parse_money(None) is None


def test_load_default_config():
    config = ReinsConfig.load("/nonexistent/path.yaml")
    assert config.storage == "duckdb"
    assert config.retention_days == 30


def test_load_yaml_config():
    yaml_content = """
budgets:
  daily: $10.00
  agents:
    my_agent:
      per_run: $0.50
      on_exceed: degrade
    strict_agent:
      per_run: $0.01
      on_exceed: reject
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        config = ReinsConfig.load(f.name)

    assert config.budget.daily == Decimal("10.00")
    assert len(config.budget.agents) == 2
    assert config.budget.agents["my_agent"].per_run == Decimal("0.50")
    assert config.budget.agents["my_agent"].on_exceed == "degrade"
    assert config.budget.agents["strict_agent"].on_exceed == "reject"


def test_get_agent_budget_fallback():
    config = ReinsConfig()
    config.budget.daily = Decimal("10")
    config.budget.on_exceed = "degrade"

    # Unknown agent should get top-level config
    budget = config.get_agent_budget("unknown")
    assert budget is not None
    assert budget.daily == Decimal("10")
    assert budget.on_exceed == "degrade"


def test_module_enabled_default():
    config = ReinsConfig()
    assert config.is_module_enabled("budget")
    assert config.is_module_enabled("nonexistent")
