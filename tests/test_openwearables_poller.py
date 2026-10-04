"""
Tests for backend.data_sources.open_wearables.poller.

Covers the two-tier polling redesign (F1/F2/F3):
- Fast-poll cursor advances to "now" only on a complete fetch.
- Fast-poll cursor is held back on a total failure (complete=False, no items).
- Fast-poll cursor advances to the last fetched sample's own event time on a
  truncated-but-partial fetch (complete=False, some items) — never past what
  wasn't actually retrieved.
- Reconciliation scheduling (_reconcile_due) and its independence from the
  fast-poll cursors.
- The optional sync-events-triggered-early-reconciliation optimization.
- is_daily_total anchor supersede-on-granular-arrival, wired end to end
  through a fast poll.
- OPENWEARABLES_ENABLED=false stays a strict no-op for the background loop.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.agent.data_store import DataStore
from backend.data_sources.open_wearables import poller
from backend.data_sources.open_wearables.client import PageResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ow_data_store(tmp_dirs) -> DataStore:
    return DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")


@pytest.fixture
def fake_state(monkeypatch):
    """Redirect poller._state()/_save_state() onto a local dict so tests
    never touch the real (module-global) app_state or disk."""
    state: dict = {}
    monkeypatch.setattr(poller, "_state", lambda: state)

    async def _noop_save():
        return None

    monkeypatch.setattr(poller, "_save_state", _noop_save)
    return state


class _FakeClient:
    """Stand-in for OpenWearablesClient returning canned PageResults."""

    def __init__(self, timeseries=None, workouts=None, sleep=None, sync_runs=None):
        self.timeseries_result = timeseries if timeseries is not None else PageResult(items=[], complete=True)
        self.workouts_result = workouts if workouts is not None else PageResult(items=[], complete=True)
        self.sleep_result = sleep if sleep is not None else PageResult(items=[], complete=True)
        self.sync_runs = sync_runs if sync_runs is not None else []
        self.timeseries_calls: list[tuple[str, str]] = []

    async def get_timeseries(self, user_id, types, start_time, end_time, **kwargs):
        self.timeseries_calls.append((start_time, end_time))
        return self.timeseries_result

    async def list_workouts(self, user_id, start_date, end_date, **kwargs):
        return self.workouts_result

    async def list_sleep_sessions(self, user_id, start_date, end_date, **kwargs):
        return self.sleep_result

    async def list_sync_run_summaries(self, user_id, limit=20):
        return self.sync_runs


def _sample(value=60, timestamp="2026-08-10T08:00:00Z", series_type="heart_rate", provider="garmin"):
    return {
        "type": series_type,
        "value": value,
        "timestamp": timestamp,
        "source": {"provider": provider, "device": "dev"},
    }


# ---------------------------------------------------------------------------
# Fast-poll cursor semantics (F1/F2/F3)
# ---------------------------------------------------------------------------


class TestFastPollCursorAdvancement:
    async def test_complete_fetch_advances_cursor_to_now(self, fake_state, ow_data_store):
        client = _FakeClient(timeseries=PageResult(items=[_sample()], complete=True))
        before = datetime.now(timezone.utc)
        result = await poller.poll_once(client, "u1", ow_data_store)
        after = datetime.now(timezone.utc)

        assert result["category_status"]["timeseries"] == "ok"
        new_cursor = datetime.fromisoformat(fake_state["cursors"]["timeseries"])
        assert before <= new_cursor <= after
        assert fake_state["counts"]["timeseries"] == 1

    async def test_total_failure_leaves_cursor_untouched(self, fake_state, ow_data_store):
        old_cursor = "2026-08-01T00:00:00+00:00"
        fake_state["cursors"] = {"timeseries": old_cursor, "workouts": old_cursor, "sleep": old_cursor}
        client = _FakeClient(timeseries=PageResult(items=[], complete=False))

        result = await poller.poll_once(client, "u1", ow_data_store)

        assert fake_state["cursors"]["timeseries"] == old_cursor
        assert result["category_status"]["timeseries"] == "error"
        assert "timeseries" in result["errors"]

    async def test_total_failure_does_not_ingest_anything(self, fake_state, ow_data_store):
        client = _FakeClient(timeseries=PageResult(items=[], complete=False))
        await poller.poll_once(client, "u1", ow_data_store)
        with ow_data_store.get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        assert row[0] == 0

    async def test_truncated_fetch_advances_cursor_to_last_fetched_sample(self, fake_state, ow_data_store):
        items = [
            _sample(value=60, timestamp="2026-08-10T08:00:00Z"),
            _sample(value=61, timestamp="2026-08-10T09:30:00Z"),  # latest event time
            _sample(value=59, timestamp="2026-08-10T07:00:00Z"),
        ]
        client = _FakeClient(timeseries=PageResult(items=items, complete=False))

        result = await poller.poll_once(client, "u1", ow_data_store)

        assert result["category_status"]["timeseries"] == "partial"
        new_cursor = datetime.fromisoformat(fake_state["cursors"]["timeseries"])
        assert new_cursor == datetime(2026, 8, 10, 9, 30, 0, tzinfo=timezone.utc)
        # Partial results are still ingested, not discarded.
        assert fake_state["counts"]["timeseries"] == 3

    async def test_first_run_backfill_respects_complete_flag(self, fake_state, ow_data_store, monkeypatch):
        """No cursor yet (first run): an incomplete backfill fetch must not
        pretend the whole backfill window was covered."""
        monkeypatch.setattr(poller.settings, "OPENWEARABLES_BACKFILL_DAYS", 7)
        client = _FakeClient(timeseries=PageResult(items=[], complete=False))
        result = await poller.poll_once(client, "u1", ow_data_store)
        assert result["category_status"]["timeseries"] == "error"
        assert "timeseries" not in fake_state["cursors"] or fake_state["cursors"]["timeseries"] is None

    async def test_overlap_window_used_on_resumed_poll(self, fake_state, ow_data_store):
        cursor = "2026-08-10T10:00:00+00:00"
        fake_state["cursors"] = {"timeseries": cursor}
        client = _FakeClient()
        await poller.poll_once(client, "u1", ow_data_store)
        start_str, _ = client.timeseries_calls[0]
        start_dt = datetime.fromisoformat(start_str)
        assert start_dt == datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc) - poller._FAST_POLL_OVERLAP

    async def test_each_category_fails_independently(self, fake_state, ow_data_store):
        client = _FakeClient(
            timeseries=PageResult(items=[], complete=False),
            workouts=PageResult(items=[], complete=True),
        )
        result = await poller.poll_once(client, "u1", ow_data_store)
        assert result["category_status"]["timeseries"] == "error"
        assert result["category_status"]["workouts"] == "ok"


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    def test_due_when_never_run(self):
        assert poller._reconcile_due({}) is True

    def test_not_due_right_after_running(self, monkeypatch):
        monkeypatch.setattr(poller.settings, "OPENWEARABLES_RECONCILE_INTERVAL", 3600)
        state = {"last_reconcile_at": poller.ts_now()}
        assert poller._reconcile_due(state) is False

    def test_due_after_interval_elapses(self, monkeypatch):
        monkeypatch.setattr(poller.settings, "OPENWEARABLES_RECONCILE_INTERVAL", 60)
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S")
        assert poller._reconcile_due({"last_reconcile_at": old}) is True

    def test_malformed_marker_treated_as_due(self):
        assert poller._reconcile_due({"last_reconcile_at": "not-a-timestamp"}) is True

    async def test_reconcile_once_uses_wide_window_independent_of_cursors(
        self, fake_state, ow_data_store, monkeypatch,
    ):
        monkeypatch.setattr(poller.settings, "OPENWEARABLES_RECONCILE_WINDOW_HOURS", 48)
        fake_state["cursors"] = {"timeseries": poller.ts_now()}  # fast-poll cursor is "now"
        client = _FakeClient(timeseries=PageResult(items=[_sample()], complete=True))

        result = await poller.reconcile_once(client, "u1", ow_data_store)

        assert result["counts"]["timeseries"] == 1
        start_str, end_str = client.timeseries_calls[0]
        window = datetime.fromisoformat(end_str) - datetime.fromisoformat(start_str)
        assert abs(window.total_seconds() - 48 * 3600) < 5
        # Reconciliation doesn't touch the fast-poll cursors.
        assert fake_state["cursors"]["timeseries"] == fake_state["cursors"]["timeseries"]
        assert fake_state["last_reconcile_status"] == "ok"

    async def test_reconcile_once_reports_incomplete_fetch(self, fake_state, ow_data_store):
        client = _FakeClient(sleep=PageResult(items=[], complete=False))
        result = await poller.reconcile_once(client, "u1", ow_data_store)
        assert "sleep" in result["errors"]
        assert fake_state["last_reconcile_status"] == "error"


class TestSyncEventsOptimization:
    async def test_returns_false_when_no_runs(self, fake_state):
        client = _FakeClient(sync_runs=[])
        assert await poller._sync_events_indicate_new_data(client, "u1", fake_state) is False

    async def test_detects_newly_completed_run(self, fake_state):
        client = _FakeClient(sync_runs=[
            {"status": "success", "ended_at": "2026-08-10T09:00:00Z"},
        ])
        found = await poller._sync_events_indicate_new_data(client, "u1", fake_state)
        assert found is True
        assert fake_state["last_sync_event_marker"] is not None

    async def test_does_not_retrigger_for_already_seen_run(self, fake_state):
        client = _FakeClient(sync_runs=[
            {"status": "success", "ended_at": "2026-08-10T09:00:00Z"},
        ])
        await poller._sync_events_indicate_new_data(client, "u1", fake_state)
        found_again = await poller._sync_events_indicate_new_data(client, "u1", fake_state)
        assert found_again is False

    async def test_ignores_in_progress_runs(self, fake_state):
        client = _FakeClient(sync_runs=[
            {"status": "in_progress", "ended_at": None},
        ])
        assert await poller._sync_events_indicate_new_data(client, "u1", fake_state) is False


# ---------------------------------------------------------------------------
# Daily-total anchor supersede, wired through a fast poll (F4)
# ---------------------------------------------------------------------------


class TestDailyTotalSupersede:
    async def test_granular_poll_deletes_stale_anchor_from_earlier_poll(self, fake_state, ow_data_store):
        # Simulate an earlier poll having stored a daily-total anchor row.
        anchor_metadata = json.dumps({"src": "ow", "provider": "garmin", "daily_total": True}, separators=(",", ":"))
        with ow_data_store.get_connection() as conn:
            conn.execute(
                "INSERT INTO samples (timestamp, feature_type, value, metadata) VALUES (?, ?, ?, ?)",
                ("2026-08-10T00:00:00", "steps", 8500, anchor_metadata),
            )
            conn.commit()

        client = _FakeClient(timeseries=PageResult(
            items=[_sample(value=500, timestamp="2026-08-10T08:00:00Z", series_type="steps")],
            complete=True,
        ))
        await poller.poll_once(client, "u1", ow_data_store)

        with ow_data_store.get_connection() as conn:
            rows = conn.execute("SELECT timestamp, value FROM samples WHERE feature_type = 'steps'").fetchall()
        # Anchor row gone, granular row present.
        assert ("2026-08-10T00:00:00", 8500) not in rows
        assert ("2026-08-10T08:00:00", 500.0) in rows


# ---------------------------------------------------------------------------
# OPENWEARABLES_ENABLED=false stays a strict no-op
# ---------------------------------------------------------------------------


class TestDisabledIsNoOp:
    async def test_poll_loop_returns_immediately_when_disabled(self, monkeypatch):
        monkeypatch.setattr(poller.settings, "OPENWEARABLES_ENABLED", False)
        created = {"client": False}

        class _ExplodingClient:
            def __init__(self, *a, **kw):
                created["client"] = True

        monkeypatch.setattr(poller, "OpenWearablesClient", _ExplodingClient)
        await poller.openwearables_poll_loop()
        assert created["client"] is False
