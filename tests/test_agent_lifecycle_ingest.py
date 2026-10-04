"""
Tests for backend.api.agent_lifecycle's watch.db -> DataStore record
building (F7 provenance): every native live-ingest record must carry
``{"src":"watch"}`` metadata so DataStore.ingest_batch()'s
``COALESCE(excluded.metadata, samples.metadata)`` upsert can never end up
with an unattributed writer overwriting another source's provenance.
"""
from __future__ import annotations

import json

from backend.api.agent_lifecycle import _WATCH_METADATA, _build_watch_records


class TestBuildWatchRecords:
    def test_every_record_carries_watch_provenance(self):
        samples = [
            {"ts": 1786435200.0, "value": 61.0, "feature_type": "heart_rate"},
            {"ts": 1786435260.0, "value": 62.0, "feature_type": "heart_rate"},
        ]
        records = _build_watch_records(samples, "LiveUser")
        assert len(records) == 2
        for record in records:
            assert json.loads(record["metadata"]) == {"src": "watch"}

    def test_metadata_is_compact_json(self):
        assert _WATCH_METADATA == '{"src":"watch"}'

    def test_preserves_value_and_feature_type(self):
        samples = [{"ts": 1786435200.0, "value": 42.5, "feature_type": "steps"}]
        records = _build_watch_records(samples, "LiveUser")
        assert records[0]["value"] == 42.5
        assert records[0]["feature_type"] == "steps"
        assert records[0]["pid"] == "LiveUser"

    def test_empty_samples_returns_empty_list(self):
        assert _build_watch_records([], "LiveUser") == []


class TestWatchRecordsFeedIngestBatch:
    """End-to-end: records built here, once ingested, are readable back with
    provenance intact — closes the loop with DataStore's COALESCE upsert."""

    def test_ingested_row_carries_watch_metadata(self, tmp_dirs):
        from backend.agent.data_store import DataStore

        store = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
        samples = [{"ts": 1786435200.0, "value": 61.0, "feature_type": "heart_rate"}]
        records = _build_watch_records(samples, "LiveUser")
        store.ingest_batch({"data": records})

        with store.get_connection() as conn:
            row = conn.execute(
                "SELECT value, metadata FROM samples WHERE feature_type = 'heart_rate'"
            ).fetchone()
        assert row is not None
        assert row[0] == 61.0
        assert json.loads(row[1]) == {"src": "watch"}
