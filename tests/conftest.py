"""Test isolation (v11): every test gets its own SQLite database.

The autouse fixture points VULCAN_DB_PATH at a per-test temp file, so no
test can ever write work orders, notifications, or sentinel state into
the repository's real data/vulcan.db — the same isolation policy the v9
audit enforced for file paths, now enforced structurally for the store.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VULCAN_DB_PATH", str(tmp_path / "test_vulcan.db"))
    yield

@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("VULCAN_LOG_DIR", str(tmp_path / "logs"))
    yield
