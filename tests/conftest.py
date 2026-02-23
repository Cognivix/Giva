"""Shared test fixtures."""

import pytest

from giva.db.store import Store
from giva.config import GivaConfig


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database."""
    return Store(tmp_path / "test.db")


@pytest.fixture
def config(tmp_path):
    """Create a test config pointing to temp directory."""
    return GivaConfig(data_dir=tmp_path)
