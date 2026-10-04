"""
Tests for backend.agent.data_store.DataStore's ingest_batch() upsert
semantics and the delete_daily_total_anchors() helper.

Covers F7 (a NULL-metadata writer — e.g. the native watch.db live-ingest
path before its own provenance-tagging fix, or any other legacy caller —
must never erase another source's provenance metadata on the same
(timestamp, feature_type) row) and the open-wearables daily-total
supersede-on-granular-arrival delete (F4).
"""
from __future__ import annotations

import json

import pytest

from backend.agent.data_store import DataStore


@pytest.fixture
def store(tmp_dirs) -> DataStore:
    return DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")


def _row(store: DataStore, timestamp: str, feature_type: str):
    with store.get_connection() as conn:
        return conn.execute(
            "SELECT value, metadata FROM samples WHERE timestamp = ? AND feature_type = ?",
            (timestamp, feature_type),
        ).fetchone()


class TestMetadataCoalesceUpsert:
    def test_null_metadata_writer_does_not_erase_existing_metadata(self, store):
        ow_meta = json.dumps({"src": "ow", "provider": "garmin"}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "heart_rate", "value": 60, "metadata": ow_meta},
        ]})
        # A different value with NULL metadata lands on the same PK (e.g. the
        # native watch path, before it tagged its own provenance).
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "heart_rate", "value": 62, "metadata": None},
        ]})
        value, metadata = _row(store, "2026-08-10T08:00:00", "heart_rate")
        assert value == 62  # new value wins
        assert json.loads(metadata) == {"src": "ow", "provider": "garmin"}  # provenance preserved

    def test_non_null_metadata_writer_overwrites(self, store):
        old_meta = json.dumps({"src": "ow", "provider": "garmin"}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "heart_rate", "value": 60, "metadata": old_meta},
        ]})
        new_meta = json.dumps({"src": "ow", "provider": "oura"}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "heart_rate", "value": 61, "metadata": new_meta},
        ]})
        value, metadata = _row(store, "2026-08-10T08:00:00", "heart_rate")
        assert value == 61
        assert json.loads(metadata) == {"src": "ow", "provider": "oura"}

    def test_fresh_insert_stores_metadata_as_given(self, store):
        meta = json.dumps({"src": "watch"}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "steps", "value": 100, "metadata": meta},
        ]})
        value, metadata = _row(store, "2026-08-10T08:00:00", "steps")
        assert value == 100
        assert json.loads(metadata) == {"src": "watch"}

    def test_unchanged_value_skips_update_but_keeps_existing_metadata(self, store):
        # The upsert's WHERE guard only fires the UPDATE when the value
        # actually changes — re-ingesting the identical value is a no-op,
        # so metadata naturally stays whatever it already was too.
        meta = json.dumps({"src": "ow", "provider": "garmin"}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "heart_rate", "value": 60, "metadata": meta},
        ]})
        store.ingest_batch({"data": [
            {"date": "2026-08-10T08:00:00", "feature_type": "heart_rate", "value": 60, "metadata": None},
        ]})
        value, metadata = _row(store, "2026-08-10T08:00:00", "heart_rate")
        assert value == 60
        assert json.loads(metadata) == {"src": "ow", "provider": "garmin"}


class TestDeleteDailyTotalAnchors:
    def test_deletes_matching_anchor_row(self, store):
        anchor_meta = json.dumps({"src": "ow", "provider": "garmin", "daily_total": True}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T00:00:00", "feature_type": "steps", "value": 8500, "metadata": anchor_meta},
        ]})
        store.delete_daily_total_anchors([("steps", "2026-08-10")])
        assert _row(store, "2026-08-10T00:00:00", "steps") is None

    def test_leaves_granular_rows_without_the_marker_untouched(self, store):
        granular_meta = json.dumps({"src": "ow", "provider": "garmin"}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T00:00:00", "feature_type": "steps", "value": 42, "metadata": granular_meta},
        ]})
        store.delete_daily_total_anchors([("steps", "2026-08-10")])
        row = _row(store, "2026-08-10T00:00:00", "steps")
        assert row is not None
        assert row[0] == 42

    def test_scoped_to_feature_type(self, store):
        anchor_meta = json.dumps({"src": "ow", "daily_total": True}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T00:00:00", "feature_type": "steps", "value": 8500, "metadata": anchor_meta},
            {"date": "2026-08-10T00:00:00", "feature_type": "active_energy", "value": 500, "metadata": anchor_meta},
        ]})
        store.delete_daily_total_anchors([("steps", "2026-08-10")])
        assert _row(store, "2026-08-10T00:00:00", "steps") is None
        assert _row(store, "2026-08-10T00:00:00", "active_energy") is not None

    def test_scoped_to_day(self, store):
        anchor_meta = json.dumps({"src": "ow", "daily_total": True}, separators=(",", ":"))
        store.ingest_batch({"data": [
            {"date": "2026-08-10T00:00:00", "feature_type": "steps", "value": 8500, "metadata": anchor_meta},
            {"date": "2026-08-11T00:00:00", "feature_type": "steps", "value": 9000, "metadata": anchor_meta},
        ]})
        store.delete_daily_total_anchors([("steps", "2026-08-10")])
        assert _row(store, "2026-08-10T00:00:00", "steps") is None
        assert _row(store, "2026-08-11T00:00:00", "steps") is not None

    def test_empty_keys_is_a_noop(self, store):
        store.delete_daily_total_anchors([])  # must not raise

    def test_no_matching_anchor_is_a_noop(self, store):
        store.delete_daily_total_anchors([("steps", "2026-08-10")])  # nothing stored yet
