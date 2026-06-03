"""
tests/conftest.py — Shared fixtures for all tests.
Each test gets an isolated in-memory SQLite DB so tests never bleed into each other.
"""

import os
import sys
import pytest

# Point imports at the app directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Give every test its own fresh SQLite DB.
    Uses a real file (not :memory:) so SQLAlchemy WAL pragma works correctly.
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("POS_CSV_PATH", "")
    monkeypatch.setenv("STORE_LAYOUT_PATH", "")

    # Re-init DB schema for this test
    import importlib
    import database
    importlib.reload(database)
    database.DB_PATH = db_file
    database.init_db()

    yield db_file
