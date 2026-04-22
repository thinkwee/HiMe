"""
Tests for backend.agent.retention — daily rolling-window pruning.

Covers the core contract of ``prune_expired_data``:
- Rows older than the cutoff are deleted from ``samples`` (health store),
  ``health_samples_eav`` (watch.db), and every target memory table.
- Rows inside the cutoff are preserved.
- A missing DB / missing table does not raise.
- ``retention_days <= 0`` is a no-op (safety rail against wipeouts).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.agent import retention

# ---------------------------------------------------------------------------
# DB builders
# ---------------------------------------------------------------------------

def _make_health_store(db_file: Path, old_iso: str, new_iso: str) -> None:
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE samples (
                timestamp TEXT NOT NULL,
                feature_type TEXT NOT NULL,
                value REAL,
                metadata TEXT,
                PRIMARY KEY (timestamp, feature_type)
            )
        """)
        conn.executemany(
            "INSERT INTO samples(timestamp, feature_type, value) VALUES (?, ?, ?)",
            [
                (old_iso, "heart_rate", 80.0),
                (new_iso, "heart_rate", 75.0),
            ],
        )
        conn.commit()


def _make_watch_db(db_file: Path, old_ts: float, new_ts: float) -> None:
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE health_samples_eav (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                f  TEXT NOT NULL,
                v  REAL NOT NULL,
                updated_at REAL NOT NULL DEFAULT 0
            )
        """)
        conn.executemany(
            "INSERT INTO health_samples_eav(ts, f, v) VALUES (?, ?, ?)",
            [(old_ts, "hr", 70.0), (new_ts, "hr", 72.0)],
        )
        conn.commit()


def _make_memory_db(db_file: Path, old_iso: str, new_iso: str) -> None:
    with sqlite3.connect(db_file) as conn:
        for tbl in ("reports", "message_evidence", "activity_log"):
            conn.execute(
                f"CREATE TABLE {tbl} (id INTEGER PRIMARY KEY, created_at TEXT)"
            )
            conn.executemany(
                f"INSERT INTO {tbl}(created_at) VALUES (?)",
                [(old_iso,), (new_iso,)],
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prune_expired_data_removes_old_rows(tmp_path: Path, monkeypatch) -> None:
    """Rows older than the cutoff must be deleted; recent rows preserved."""
    # Build a self-contained fake repo tree: data_stores, memory, ios/Server.
    data_stores = tmp_path / "data_stores"
    memory_dir = tmp_path / "memory"
    watch_dir = tmp_path / "ios" / "Server"
    for d in (data_stores, memory_dir, watch_dir):
        d.mkdir(parents=True)

    now = datetime.now(timezone.utc)
    old_dt = now - timedelta(days=60)
    new_dt = now - timedelta(days=1)
    old_iso = old_dt.strftime("%Y-%m-%dT%H:%M:%S")
    new_iso = new_dt.strftime("%Y-%m-%dT%H:%M:%S")

    _make_health_store(data_stores / "LiveUser_data.db", old_iso, new_iso)
    _make_memory_db(memory_dir / "LiveUser.db", old_iso, new_iso)
    _make_watch_db(watch_dir / "watch.db", old_dt.timestamp(), new_dt.timestamp())

    # Point retention at our fake tree: settings paths for data/memory,
    # and monkeypatch _repo_root so ios/Server/watch.db resolves into tmp.
    monkeypatch.setattr(retention.settings, "DATA_STORE_PATH", data_stores)
    monkeypatch.setattr(retention.settings, "MEMORY_DB_PATH", memory_dir)
    monkeypatch.setattr(retention, "_repo_root", lambda: tmp_path)

    results = await retention.prune_expired_data(retention_days=30)

    # Every attempted (db, table) pair should report a deletion count.
    assert results["LiveUser_data.db:samples"] == 1
    assert results["watch.db:health_samples_eav"] == 1
    for tbl in ("reports", "message_evidence", "activity_log"):
        assert results[f"LiveUser.db:{tbl}"] == 1

    # Verify the new rows are still there and the old ones are gone.
    with sqlite3.connect(data_stores / "LiveUser_data.db") as conn:
        rows = conn.execute("SELECT timestamp FROM samples").fetchall()
        assert rows == [(new_iso,)]

    with sqlite3.connect(watch_dir / "watch.db") as conn:
        rows = conn.execute("SELECT ts FROM health_samples_eav").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(new_dt.timestamp())

    with sqlite3.connect(memory_dir / "LiveUser.db") as conn:
        for tbl in ("reports", "message_evidence", "activity_log"):
            rows = conn.execute(f"SELECT created_at FROM {tbl}").fetchall()
            assert rows == [(new_iso,)], f"{tbl} was not pruned correctly"


@pytest.mark.asyncio
async def test_prune_expired_data_noop_when_disabled(tmp_path: Path, monkeypatch) -> None:
    """retention_days <= 0 must be a no-op (safety rail)."""
    data_stores = tmp_path / "data_stores"
    data_stores.mkdir()
    old_iso_a = "2000-01-01T00:00:00"
    old_iso_b = "2000-01-02T00:00:00"
    _make_health_store(data_stores / "x_data.db", old_iso_a, old_iso_b)

    monkeypatch.setattr(retention.settings, "DATA_STORE_PATH", data_stores)
    monkeypatch.setattr(retention.settings, "MEMORY_DB_PATH", tmp_path / "memory_missing")
    monkeypatch.setattr(retention, "_repo_root", lambda: tmp_path)

    results = await retention.prune_expired_data(retention_days=0)
    assert results == {}

    with sqlite3.connect(data_stores / "x_data.db") as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        assert count == 2  # nothing was touched


@pytest.mark.asyncio
async def test_prune_expired_data_missing_dirs_ok(tmp_path: Path, monkeypatch) -> None:
    """Missing data / memory directories and missing watch.db must not raise."""
    monkeypatch.setattr(retention.settings, "DATA_STORE_PATH", tmp_path / "nope_data")
    monkeypatch.setattr(retention.settings, "MEMORY_DB_PATH", tmp_path / "nope_memory")
    monkeypatch.setattr(retention, "_repo_root", lambda: tmp_path)

    results = await retention.prune_expired_data(retention_days=30)
    assert results == {}
