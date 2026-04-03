"""Test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from reins.core.config import ReinsConfig
from reins.core.events import EventBus
from reins.core.storage import Storage


@pytest.fixture
def tmp_db(tmp_path):
    """Temporary DuckDB database."""
    return tmp_path / "test.duckdb"


@pytest.fixture
def storage(tmp_db):
    """Storage with temporary database."""
    s = Storage(tmp_db)
    yield s
    s.close()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def config():
    return ReinsConfig()
