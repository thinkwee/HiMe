"""
Tests for the HIME data pipeline.

Covers:
- WatchDBReader: reading from watch.db (EAV format), handling missing DB
- DataStoreReader: reading from health_data.db (DataStore format)
- DataStore: ingestion, stats, metadata tracking
- MemoryManager: schema creation, reports, activity log, trimming
"""
from __future__ import annotations

import json
import sqlite3
import time

import pandas as pd
import pytest

from backend.agent.data_store import DataStore
from backend.agent.memory_manager import MemoryManager, _ensure_schema
from backend.data_readers.data_store_reader import DataStoreReader
from backend.data_readers.watch_db_reader import WatchDBReader

# =========================================================================
# WatchDBReader (watch.db — EAV format)
# =========================================================================

class TestWatchDBReader:
    """Tests for the WatchDBReader that reads from the WatchExporter watch.db."""

    @pytest.fixture
    def reader(self, tmp_dirs, watch_db) -> WatchDBReader:
        return WatchDBReader(tmp_dirs["watch"])

    def test_init_creates_directory(self, tmp_path):
        """WatchDBReader should create the data directory if it does not exist."""
        new_dir = tmp_path / "nonexistent"
        WatchDBReader(new_dir)
        assert new_dir.exists()

    def test_get_available_users(self, reader):
        """Always returns ['LiveUser'] for live mode."""
        assert reader.get_available_users() == ["LiveUser"]

    def test_get_feature_types_with_db(self, reader):
        """Should return the distinct feature types from watch.db."""
        features = reader.get_feature_types()
        assert isinstance(features, list)
        assert len(features) > 0
        # Our fixture inserts heart_rate, steps, active_energy
        for expected in ["heart_rate", "steps", "active_energy"]:
            assert expected in features

    def test_get_feature_types_without_db(self, tmp_path):
        """When watch.db is missing, should return the default feature list."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        reader = WatchDBReader(empty_dir)
        features = reader.get_feature_types()
        assert "heart_rate" in features
        assert "steps" in features
        assert len(features) > 20  # default list is large

    def test_get_feature_columns(self, reader):
        """Live data uses a single 'value' column."""
        assert reader.get_feature_columns("heart_rate") == ["value"]

    def test_get_date_range(self, reader):
        """Should return a valid (min, max) date range."""
        min_dt, max_dt = reader.get_date_range(["LiveUser"])
        assert min_dt < max_dt
        assert isinstance(min_dt, pd.Timestamp)

    def test_get_date_range_missing_db(self, tmp_path):
        """When watch.db is missing, returns a default 1-hour window."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        reader = WatchDBReader(empty_dir)
        min_dt, max_dt = reader.get_date_range(["LiveUser"])
        assert max_dt > min_dt

    def test_load_feature_data(self, reader):
        """Loading feature data should return a DataFrame with expected columns."""
        # Use a large window so all fixture data is included
        df = reader.load_feature_data(
            ["LiveUser"], "heart_rate", minutes=1440
        )
        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert "date" in df.columns
            assert "value" in df.columns
            assert "feature_type" in df.columns
            assert "pid" in df.columns
            assert "ts" in df.columns

    def test_load_feature_data_missing_db(self, tmp_path):
        """When watch.db does not exist, returns an empty DataFrame."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        reader = WatchDBReader(empty_dir)
        df = reader.load_feature_data(["LiveUser"], "heart_rate")
        assert df.empty

    def test_get_all_samples_since_id(self, reader):
        """get_all_samples_since_id should return records after the given ID."""
        samples = reader.get_all_samples_since_id(0)
        assert isinstance(samples, list)
        assert len(samples) > 0
        assert "id" in samples[0]
        assert "feature_type" in samples[0]
        assert "ts" in samples[0]
        assert "value" in samples[0]

        # Getting samples after a high ID should return fewer results
        high_id = samples[-1]["id"]
        later_samples = reader.get_all_samples_since_id(high_id)
        assert len(later_samples) == 0

    def test_get_all_samples_since(self, reader):
        """get_all_samples_since returns records newer than the given timestamp."""
        samples = reader.get_all_samples_since(0.0)
        assert len(samples) > 0

    def test_get_latest_samples(self, reader):
        """get_latest_samples returns the most recent sample per feature."""
        latest = reader.get_latest_samples()
        assert isinstance(latest, dict)
        assert len(latest) > 0
        for _feature, info in latest.items():
            assert "ts" in info
            assert "value" in info

    def test_load_feature_data_with_since_ts(self, reader):
        """Loading data with since_ts should filter by timestamp."""
        # Get all data first to find a reasonable cutoff
        all_data = reader.load_feature_data(["LiveUser"], "heart_rate", minutes=1440)
        if not all_data.empty:
            mid_ts = all_data["ts"].median()
            filtered = reader.load_feature_data(
                ["LiveUser"], "heart_rate", since_ts=mid_ts
            )
            assert len(filtered) <= len(all_data)


# =========================================================================
# DataStoreReader (DataStore health_data.db)
# =========================================================================

class TestDataStoreReader:
    """Tests for DataStoreReader that reads from the DataStore format."""

    @pytest.fixture
    def reader(self, tmp_dirs, health_data_db) -> DataStoreReader:
        return DataStoreReader(tmp_dirs["data_stores"])

    def test_get_available_users(self, reader):
        """Should discover LiveUser from the filename pattern."""
        pids = reader.get_available_users()
        assert "LiveUser" in pids

    def test_get_feature_types(self, reader):
        """Should return feature types from the DB plus defaults."""
        features = reader.get_feature_types()
        assert isinstance(features, list)
        assert len(features) > 0
        # Our fixture has these features
        assert "heart_rate" in features
        assert "steps" in features

    def test_get_feature_columns(self, reader):
        """Always returns ['value'] for this format."""
        assert reader.get_feature_columns("heart_rate") == ["value"]

    def test_get_date_range(self, reader):
        """Should return a date range from the DB."""
        min_dt, max_dt = reader.get_date_range(["LiveUser"])
        assert min_dt is not None
        assert max_dt is not None

    def test_load_feature_data(self, reader):
        """Should load feature data with correct columns."""
        df = reader.load_feature_data(
            ["LiveUser"], "heart_rate", minutes=1500
        )
        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert "date" in df.columns
            assert "value" in df.columns
            assert "feature_type" in df.columns
            assert "pid" in df.columns
            assert "ts" in df.columns

    def test_load_feature_data_empty_pids(self, reader):
        """Empty user list should return empty DataFrame."""
        df = reader.load_feature_data([], "heart_rate")
        assert df.empty

    def test_load_features_batch(self, reader):
        """Batch loading should return combined data for multiple features."""
        df = reader.load_features_batch(
            ["LiveUser"],
            ["heart_rate", "steps"],
            minutes=1500,
        )
        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert set(df["feature_type"].unique()) <= {"heart_rate", "steps"}

    def test_load_features_batch_with_since_ts(self, reader):
        """Batch loading with since_ts should filter data."""
        df = reader.load_features_batch(
            ["LiveUser"],
            ["heart_rate"],
            since_ts=time.time() - 86400,  # last 24 hours
        )
        assert isinstance(df, pd.DataFrame)

    def test_handles_missing_db_gracefully(self, tmp_path):
        """If the user DB does not exist, load_feature_data returns empty DataFrame."""
        data_dir = tmp_path / "data_stores"
        data_dir.mkdir()
        reader = DataStoreReader(data_dir)
        df = reader.load_feature_data(["NewUser"], "heart_rate")
        assert isinstance(df, pd.DataFrame)
        assert df.empty


# =========================================================================
# DataStore
# =========================================================================

class TestDataStore:
    """Tests for the DataStore (health data ingestion and querying)."""

    @pytest.fixture
    def store(self, tmp_dirs) -> DataStore:
        return DataStore(db_path=tmp_dirs["data_stores"], user_id="TestUser")

    def test_init_creates_db(self, store, tmp_dirs):
        """DataStore should create the SQLite database on init."""
        db_file = tmp_dirs["data_stores"] / "TestUser_data.db"
        assert db_file.exists()

    def test_init_creates_tables(self, store):
        """DataStore should create health_data and store_metadata tables."""
        conn = store.get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = [t[0] for t in tables]
        assert "samples" in table_names
        assert "store_metadata" in table_names

    def test_ingest_batch_feature_type_format(self, store):
        """Ingest records with feature_type key (Apple Health format)."""
        batch = {
            "data": [
                {
                    "date": "2026-03-20T10:00:00",
                    "value": 72.5,
                    "feature_type": "heart_rate",
                    "pid": "TestUser",
                },
                {
                    "date": "2026-03-20T10:05:00",
                    "value": 150.0,
                    "feature_type": "steps",
                    "pid": "TestUser",
                },
            ],
            "data_timestamp": "2026-03-20T10:05:00",
            "num_records": 2,
            "is_live": True,
        }
        store.ingest_batch(batch)

        stats = store.get_stats()
        assert stats["total_records"] == 2
        assert "heart_rate" in stats["by_feature"]
        assert "steps" in stats["by_feature"]

    def test_ingest_batch_wide_format(self, store):
        """Ingest records in wide format (multiple numeric columns per row)."""
        batch = {
            "data": [
                {
                    "date": "2026-03-20T11:00:00",
                    "heart_rate": 75.0,
                    "steps": 200.0,
                    "pid": "TestUser",
                },
            ],
            "data_timestamp": "2026-03-20T11:00:00",
        }
        store.ingest_batch(batch)
        stats = store.get_stats()
        assert stats["total_records"] >= 2  # heart_rate and steps from the wide row

    def test_ingest_batch_skips_none_values(self, store):
        """Records with None values should be skipped."""
        batch = {
            "data": [
                {
                    "date": "2026-03-20T12:00:00",
                    "value": None,
                    "feature_type": "heart_rate",
                    "pid": "TestUser",
                },
            ],
        }
        store.ingest_batch(batch)
        stats = store.get_stats()
        assert stats["total_records"] == 0

    def test_ingest_batch_empty_data(self, store):
        """Empty data list should not crash."""
        store.ingest_batch({"data": [], "data_timestamp": None})
        stats = store.get_stats()
        assert stats["total_records"] == 0

    def test_save_and_get_ingestion_progress(self, store):
        """save_ingestion_progress and get_last_ingestion_time should round-trip."""
        ts = "2026-03-20T10:00:00"
        store.save_ingestion_progress(ts)
        assert store.get_last_ingestion_time() == ts

    def test_save_and_get_ingestion_id(self, store):
        """save_ingestion_id and get_last_ingested_id should round-trip."""
        store.save_ingestion_id(12345)
        assert store.get_last_ingested_id() == 12345

    def test_get_last_ingested_id_default(self, store):
        """Default ingested ID should be 0 when nothing has been saved."""
        assert store.get_last_ingested_id() == 0

    def test_query(self, store):
        """store.query() should return a DataFrame."""
        store.ingest_batch({
            "data": [
                {
                    "date": "2026-03-20T13:00:00",
                    "value": 80.0,
                    "feature_type": "heart_rate",
                    "pid": "TestUser",
                },
            ],
        })
        df = store.query("SELECT * FROM samples WHERE feature_type = 'heart_rate'")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert df.iloc[0]["value"] == 80.0

    def test_get_stats_empty(self, store):
        """Stats on an empty store should return zeros."""
        stats = store.get_stats()
        assert stats["total_records"] == 0
        assert stats["by_feature"] == {}

    def test_get_stats_with_data(self, store):
        """Stats should reflect ingested data."""
        records = [
            {"date": f"2026-03-20T{h:02d}:00:00", "value": 70.0 + h,
             "feature_type": "heart_rate", "pid": "TestUser"}
            for h in range(10)
        ]
        store.ingest_batch({"data": records})
        stats = store.get_stats()
        assert stats["total_records"] == 10
        assert stats["by_feature"]["heart_rate"] == 10
        assert stats["time_range"]["min"] is not None
        assert stats["time_range"]["max"] is not None

    def test_stop_ingestion(self, store):
        """stop_ingestion should set the flag to False."""
        store.is_ingesting = True
        store.stop_ingestion()
        assert store.is_ingesting is False

    def test_get_connection(self, store):
        """get_connection should return a usable SQLite connection."""
        conn = store.get_connection()
        result = conn.execute("SELECT 1").fetchone()
        conn.close()
        assert result[0] == 1

    def test_ingest_batch_dedup(self, store):
        """Ingesting the same (timestamp, feature_type) with a new value should UPSERT (last write wins)."""
        batch1 = {
            "data": [
                {"date": "2026-03-20T14:00:00", "value": 65.0,
                 "feature_type": "heart_rate", "pid": "TestUser"},
            ],
        }
        batch2 = {
            "data": [
                {"date": "2026-03-20T14:00:00", "value": 99.0,
                 "feature_type": "heart_rate", "pid": "TestUser"},
            ],
        }
        store.ingest_batch(batch1)
        store.ingest_batch(batch2)
        stats = store.get_stats()
        # Should still be 1 record (UPSERT, not duplicate), not 2
        assert stats["total_records"] == 1
        df = store.query("SELECT value FROM samples WHERE feature_type = 'heart_rate'")
        # Last-write wins: the updated value (99.0) replaces the original
        assert df.iloc[0]["value"] == 99.0


# =========================================================================
# MemoryManager
# =========================================================================

class TestMemoryManager:
    """Tests for the MemoryManager (reports, activity log, schema)."""

    def test_init_creates_db(self, tmp_path):
        """MemoryManager should create the DB file and schema on init."""
        db_dir = tmp_path / "memory"
        db_dir.mkdir()
        mm = MemoryManager(db_dir, "TestUser")
        assert mm.db_file.exists()

        # Verify tables exist
        with sqlite3.connect(mm.db_file) as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        for expected in ("reports", "activity_log", "scheduled_tasks", "personalised_pages"):
            assert expected in tables, f"Missing table: {expected}"

    def test_get_recent_reports(self, memory_manager):
        """Should return the pre-seeded test report."""
        reports = memory_manager.get_recent_reports(limit=10)
        assert len(reports) >= 1
        assert reports[0]["title"] == "Test Report"
        assert reports[0]["alert_level"] == "normal"

    def test_get_recent_reports_with_metadata(self, memory_manager):
        """Reports with JSON metadata should be parsed."""
        with sqlite3.connect(memory_manager.db_file) as conn:
            conn.execute(
                "INSERT INTO reports (title, content, alert_level, metadata) VALUES (?, ?, ?, ?)",
                ("Meta Report", "Content", "info", json.dumps({"key": "value"})),
            )
            conn.commit()
        reports = memory_manager.get_recent_reports(limit=10)
        meta_report = next(r for r in reports if r["title"] == "Meta Report")
        assert isinstance(meta_report["metadata"], dict)
        assert meta_report["metadata"]["key"] == "value"

    def test_get_recent_activity(self, memory_manager):
        """Should return the pre-seeded activity events."""
        events = memory_manager.get_recent_activity(limit=100)
        assert len(events) >= 5
        # Events should be in chronological order (oldest first)
        assert events[0]["type"] == "test_event_0"

    async def test_persist_activity(self, memory_manager):
        """persist_activity should insert a new event into the activity_log."""
        event = {"type": "test_persist", "data": "async persistence test"}
        await memory_manager.persist_activity(event)

        events = memory_manager.get_recent_activity(limit=100)
        types = [e["type"] for e in events]
        assert "test_persist" in types

    async def test_activity_log_trimming(self, tmp_path):
        """Activity log should be trimmed to ACTIVITY_LIMIT rows."""
        db_dir = tmp_path / "memory"
        db_dir.mkdir()
        mm = MemoryManager(db_dir, "TrimUser")

        # Lower the limit for testing
        original_limit = mm.ACTIVITY_LIMIT
        mm.ACTIVITY_LIMIT = 10

        try:
            # Insert more than the limit
            for i in range(15):
                await mm.persist_activity({"type": f"event_{i}", "index": i})

            with sqlite3.connect(mm.db_file) as conn:
                count = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
            assert count <= 10
        finally:
            mm.ACTIVITY_LIMIT = original_limit

    def test_get_stats(self, memory_manager):
        """get_stats should return table counts and date range."""
        stats = memory_manager.get_stats()
        assert "table_counts" in stats
        assert "date_range" in stats
        assert "reports" in stats["table_counts"]
        assert stats["table_counts"]["reports"] >= 1

    def test_ensure_schema_idempotent(self, memory_manager):
        """Calling _ensure_schema multiple times should not fail."""
        with sqlite3.connect(memory_manager.db_file) as conn:
            _ensure_schema(conn)
            _ensure_schema(conn)
            _ensure_schema(conn)
        # Should not raise

    def test_multiple_managers_same_db(self, tmp_path):
        """Multiple MemoryManager instances for the same user should co-exist."""
        db_dir = tmp_path / "memory"
        db_dir.mkdir()
        mm1 = MemoryManager(db_dir, "SharedUser")
        mm2 = MemoryManager(db_dir, "SharedUser")

        # Write with mm1, read with mm2
        with sqlite3.connect(mm1.db_file) as conn:
            conn.execute(
                "INSERT INTO reports (title, content) VALUES (?, ?)",
                ("Cross-instance", "Test content"),
            )
            conn.commit()

        reports = mm2.get_recent_reports()
        assert any(r["title"] == "Cross-instance" for r in reports)
