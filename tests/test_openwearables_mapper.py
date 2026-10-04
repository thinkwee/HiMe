"""
Tests for backend.data_sources.open_wearables.mapper — pure functions that
turn open-wearables payloads into DataStore.ingest_batch() rows.

Covers: unit conversion (both directions of the storage-vs-display-unit
mismatch), timestamp normalization (UTC, naive, seconds precision — HiMe's
canonical convention per backend/utils.py), provider filtering (native Apple
Watch data dropped by default; allowlist support), is_daily_total handling
(granular data supersedes same-batch daily totals, scoped per
feature/provider/day), sleep session mapping (stage intervals preferred over
the summary fallback, end-time anchoring, staged-vs-unstaged total
suppression), and workout mapping (known category + generic fallback).
"""
from __future__ import annotations

import json

from backend.data_sources.open_wearables import mapper

# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------


class TestTimestampNormalization:
    def test_zulu_suffix(self):
        assert mapper.to_hime_ts("2026-08-10T08:00:00Z") == "2026-08-10T08:00:00"

    def test_explicit_offset_converted_to_utc(self):
        # 10:00 +02:00 == 08:00 UTC
        assert mapper.to_hime_ts("2026-08-10T10:00:00+02:00") == "2026-08-10T08:00:00"

    def test_naive_timestamp_uses_zone_offset_param(self):
        # Naive "10:00" with zone_offset "+02:00" -> 08:00 UTC
        assert mapper.to_hime_ts("2026-08-10T10:00:00", zone_offset="+02:00") == "2026-08-10T08:00:00"

    def test_naive_timestamp_without_zone_offset_assumes_utc(self):
        assert mapper.to_hime_ts("2026-08-10T08:00:00") == "2026-08-10T08:00:00"

    def test_microseconds_truncated_to_seconds(self):
        assert mapper.to_hime_ts("2026-08-10T08:00:00.123456Z") == "2026-08-10T08:00:00"

    def test_unparseable_timestamp_returns_none(self):
        assert mapper.to_hime_ts("not-a-timestamp") is None

    def test_none_timestamp_returns_none(self):
        assert mapper.to_hime_ts(None) is None


# ---------------------------------------------------------------------------
# Timeseries sample mapping — unit conversion + passthrough + skip
# ---------------------------------------------------------------------------


def _sample(series_type, value, provider="garmin", **extra):
    s = {
        "timestamp": "2026-08-10T08:00:00Z",
        "type": series_type,
        "value": value,
        "unit": "n/a",
        "source": {"provider": provider, "device": "test-device"},
    }
    s.update(extra)
    return s


class TestTimeseriesMapping:
    def test_direct_passthrough_no_conversion(self):
        rows = mapper.map_timeseries_samples([_sample("heart_rate", 62)])
        assert len(rows) == 1
        assert rows[0]["feature_type"] == "heart_rate"
        assert rows[0]["value"] == 62.0

    def test_oxygen_saturation_percent_to_fraction(self):
        rows = mapper.map_timeseries_samples([_sample("oxygen_saturation", 98)])
        assert rows[0]["feature_type"] == "blood_oxygen"
        assert rows[0]["value"] == 0.98

    def test_stand_time_minutes_to_seconds(self):
        rows = mapper.map_timeseries_samples([_sample("stand_time", 12)])
        assert rows[0]["feature_type"] == "stand_time"
        assert rows[0]["value"] == 720.0

    def test_exercise_time_minutes_to_seconds(self):
        rows = mapper.map_timeseries_samples([_sample("exercise_time", 30)])
        assert rows[0]["value"] == 1800.0

    def test_walking_step_length_cm_to_m(self):
        rows = mapper.map_timeseries_samples([_sample("walking_step_length", 70)])
        assert rows[0]["feature_type"] == "walking_step_length"
        assert abs(rows[0]["value"] - 0.7) < 1e-9

    def test_walking_asymmetry_percentage_to_fraction(self):
        rows = mapper.map_timeseries_samples([_sample("walking_asymmetry_percentage", 4.5)])
        assert rows[0]["feature_type"] == "walking_asymmetry"
        assert abs(rows[0]["value"] - 0.045) < 1e-9

    def test_time_in_daylight_minutes_to_seconds(self):
        rows = mapper.map_timeseries_samples([_sample("time_in_daylight", 45)])
        assert rows[0]["value"] == 2700.0

    def test_hydration_maps_to_water(self):
        rows = mapper.map_timeseries_samples([_sample("hydration", 250)])
        assert rows[0]["feature_type"] == "water"
        assert rows[0]["value"] == 250.0

    def test_new_ow_only_feature_passthrough(self):
        rows = mapper.map_timeseries_samples([_sample("garmin_body_battery", 72)])
        assert rows[0]["feature_type"] == "garmin_body_battery"
        assert rows[0]["value"] == 72.0

    def test_metadata_json_shape(self):
        rows = mapper.map_timeseries_samples([_sample("heart_rate", 62, provider="garmin")])
        meta = json.loads(rows[0]["metadata"])
        assert meta["src"] == "ow"
        assert meta["provider"] == "garmin"
        assert meta["device"] == "test-device"

    def test_unmapped_series_type_skipped(self):
        rows = mapper.map_timeseries_samples([_sample("physical_effort", 3.2)])
        assert rows == []

    def test_unknown_series_type_skipped(self):
        rows = mapper.map_timeseries_samples([_sample("totally_unknown_metric", 1)])
        assert rows == []

    def test_missing_value_skipped(self):
        rows = mapper.map_timeseries_samples([_sample("heart_rate", None)])
        assert rows == []

    def test_missing_timestamp_skipped(self):
        s = _sample("heart_rate", 60)
        s["timestamp"] = None
        assert mapper.map_timeseries_samples([s]) == []


# ---------------------------------------------------------------------------
# is_daily_total handling (F4) — granular data is authoritative
# ---------------------------------------------------------------------------


class TestDailyTotalHandling:
    def test_daily_total_dropped_when_granular_present_same_batch(self):
        samples = [
            _sample("steps", 500, provider="garmin", timestamp="2026-08-10T08:00:00Z"),
            _sample("steps", 8500, provider="garmin", timestamp="2026-08-10T00:00:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 1
        assert rows[0]["value"] == 500.0

    def test_daily_total_kept_when_no_granular_for_that_day(self):
        samples = [_sample("steps", 8500, provider="garmin", timestamp="2026-08-10T12:00:00Z", is_daily_total=True)]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 1
        assert rows[0]["value"] == 8500.0
        # Deterministic day-anchor, not the provider's arbitrary timestamp.
        assert rows[0]["date"] == "2026-08-10T00:00:00"
        meta = json.loads(rows[0]["metadata"])
        assert meta["daily_total"] is True

    def test_daily_total_scoped_per_feature(self):
        # A granular *heart_rate* sample must not suppress a *steps* daily total.
        samples = [
            _sample("heart_rate", 61, provider="garmin", timestamp="2026-08-10T08:00:00Z"),
            _sample("steps", 8500, provider="garmin", timestamp="2026-08-10T00:00:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        feature_types = {r["feature_type"] for r in rows}
        assert feature_types == {"heart_rate", "steps"}

    def test_daily_total_scoped_per_provider(self):
        # Granular data from one provider must not suppress another
        # provider's daily total for the same feature/day.
        samples = [
            _sample("steps", 500, provider="garmin", timestamp="2026-08-10T08:00:00Z"),
            _sample("steps", 9000, provider="oura", timestamp="2026-08-10T00:00:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 2

    def test_daily_total_scoped_per_day(self):
        samples = [
            _sample("steps", 500, provider="garmin", timestamp="2026-08-11T08:00:00Z"),
            _sample("steps", 8500, provider="garmin", timestamp="2026-08-10T00:00:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 2

    def test_multiple_daily_total_rows_same_day_are_summed(self):
        # Some providers (observed live against open-wearables' own seed
        # data: Polar's continuous energy feed) report a day's total as many
        # same-day is_daily_total=true chunks instead of one row. Without
        # summing, every chunk collapses onto the same day-anchor
        # (feature_type, timestamp) key and DataStore.ingest_batch()'s
        # upsert keeps only the last one in batch order — silently
        # discarding the rest instead of producing the true total.
        samples = [
            _sample("energy", 10, provider="polar", timestamp="2026-08-10T00:00:00Z", is_daily_total=True),
            _sample("energy", 15, provider="polar", timestamp="2026-08-10T00:05:00Z", is_daily_total=True),
            _sample("energy", 7, provider="polar", timestamp="2026-08-10T23:55:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 1
        assert rows[0]["value"] == 32.0
        assert rows[0]["date"] == "2026-08-10T00:00:00"
        meta = json.loads(rows[0]["metadata"])
        assert meta["daily_total"] is True

    def test_multiple_daily_total_rows_scoped_per_day_and_provider(self):
        samples = [
            _sample("energy", 10, provider="polar", timestamp="2026-08-10T00:05:00Z", is_daily_total=True),
            _sample("energy", 20, provider="polar", timestamp="2026-08-11T00:05:00Z", is_daily_total=True),
            _sample("energy", 30, provider="oura", timestamp="2026-08-10T00:05:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 3
        values = {(r["date"], json.loads(r["metadata"])["provider"]): r["value"] for r in rows}
        assert values[("2026-08-10T00:00:00", "polar")] == 10.0
        assert values[("2026-08-11T00:00:00", "polar")] == 20.0
        assert values[("2026-08-10T00:00:00", "oura")] == 30.0

    def test_daily_total_aggregation_then_dropped_when_granular_present(self):
        # Aggregation (summing multiple same-day daily-total rows) must run
        # before the granular-supersedes-daily-total rule, so a granular
        # sample still wins even when the daily total was split across
        # several rows.
        samples = [
            _sample("steps", 500, provider="garmin", timestamp="2026-08-10T08:00:00Z"),
            _sample("steps", 4000, provider="garmin", timestamp="2026-08-10T00:00:00Z", is_daily_total=True),
            _sample("steps", 4500, provider="garmin", timestamp="2026-08-10T12:00:00Z", is_daily_total=True),
        ]
        rows = mapper.map_timeseries_samples(samples)
        assert len(rows) == 1
        assert rows[0]["value"] == 500.0

    def test_webhook_batch_applies_same_daily_total_rules(self):
        data = {
            "provider": "garmin",
            "series_type": "steps",
            "samples": [
                {"timestamp": "2026-08-10T08:00:00Z", "value": 500},
                {"timestamp": "2026-08-10T00:00:00Z", "value": 8500, "is_daily_total": True},
            ],
        }
        rows = mapper.map_webhook_timeseries_event(data)
        assert len(rows) == 1
        assert rows[0]["value"] == 500.0


# ---------------------------------------------------------------------------
# Provider filtering
# ---------------------------------------------------------------------------


class TestProviderFiltering:
    def test_apple_dropped_by_default(self):
        rows = mapper.map_timeseries_samples([_sample("heart_rate", 60, provider="apple")])
        assert rows == []

    def test_apple_health_alias_dropped_by_default(self):
        rows = mapper.map_timeseries_samples([_sample("heart_rate", 60, provider="apple_health")])
        assert rows == []

    def test_non_apple_provider_kept_without_allowlist(self):
        rows = mapper.map_timeseries_samples([_sample("heart_rate", 60, provider="oura")])
        assert len(rows) == 1

    def test_allowlist_keeps_matching_provider(self):
        rows = mapper.map_timeseries_samples(
            [_sample("heart_rate", 60, provider="garmin")],
            provider_allowlist=frozenset({"garmin"}),
        )
        assert len(rows) == 1

    def test_allowlist_drops_non_matching_provider(self):
        rows = mapper.map_timeseries_samples(
            [_sample("heart_rate", 60, provider="oura")],
            provider_allowlist=frozenset({"garmin"}),
        )
        assert rows == []

    def test_allowlist_still_drops_apple(self):
        rows = mapper.map_timeseries_samples(
            [_sample("heart_rate", 60, provider="apple")],
            provider_allowlist=frozenset({"apple"}),  # explicit allowlist can't override the hard exclusion
        )
        assert rows == []


# ---------------------------------------------------------------------------
# Webhook-shaped timeseries batch
# ---------------------------------------------------------------------------


class TestWebhookTimeseriesMapping:
    def test_batch_with_top_level_provider_and_series_type(self):
        data = {
            "user_id": "u1",
            "provider": "garmin",
            "series_type": "heart_rate",
            "samples": [
                {"timestamp": "2026-08-10T08:00:00Z", "type": "heart_rate", "value": 58, "unit": "bpm"},
                {"timestamp": "2026-08-10T08:05:00Z", "value": 60, "unit": "bpm"},  # falls back to series_type
            ],
        }
        rows = mapper.map_webhook_timeseries_event(data)
        assert len(rows) == 2
        assert {r["value"] for r in rows} == {58.0, 60.0}
        assert all(r["feature_type"] == "heart_rate" for r in rows)

    def test_batch_provider_filtering_applies(self):
        data = {
            "provider": "apple",
            "series_type": "heart_rate",
            "samples": [{"timestamp": "2026-08-10T08:00:00Z", "value": 58}],
        }
        assert mapper.map_webhook_timeseries_event(data) == []

    def test_empty_data(self):
        assert mapper.map_webhook_timeseries_event({}) == []
        assert mapper.map_webhook_timeseries_event(None) == []


# ---------------------------------------------------------------------------
# Sleep session mapping
# ---------------------------------------------------------------------------


class TestSleepSessionMapping:
    def _session(self, **overrides):
        session = {
            "id": "sess-1",
            "start_time": "2026-08-10T22:00:00Z",
            "end_time": "2026-08-11T06:00:00Z",
            "zone_offset": "+00:00",
            "source": {"provider": "oura", "device": "ring"},
            "duration_seconds": 8 * 3600,
            "sleep_duration_seconds": 7 * 3600,
            "efficiency_percent": 91.5,
            "is_nap": False,
        }
        session.update(overrides)
        return session

    def test_uses_stage_intervals_when_present(self):
        session = self._session(sleep_stage_intervals=[
            {"stage": "awake", "start_time": "2026-08-10T22:00:00Z", "end_time": "2026-08-10T22:10:00Z"},
            {"stage": "light", "start_time": "2026-08-10T22:10:00Z", "end_time": "2026-08-11T01:10:00Z"},
            {"stage": "deep", "start_time": "2026-08-11T01:10:00Z", "end_time": "2026-08-11T02:40:00Z"},
            {"stage": "rem", "start_time": "2026-08-11T02:40:00Z", "end_time": "2026-08-11T04:00:00Z"},
            {"stage": "in_bed", "start_time": "2026-08-11T04:00:00Z", "end_time": "2026-08-11T06:00:00Z"},
        ])
        rows = mapper.map_sleep_session(session)
        by_feature = {r["feature_type"]: r["value"] for r in rows}
        # Staged session: no session-total sleep_asleep row (F5) — it would
        # double-count against the stage rows.
        assert "sleep_asleep" not in by_feature
        assert by_feature["sleep_in_bed"] == 8 * 3600
        assert by_feature["sleep_efficiency"] == 91.5
        assert by_feature["sleep_awake"] == 600.0
        assert by_feature["sleep_core"] == 3 * 3600.0
        assert by_feature["sleep_deep"] == 90 * 60.0
        assert by_feature["sleep_rem"] == 80 * 60.0
        # "in_bed" interval isn't a specific stage -> not emitted as its own row
        assert len(rows) == 6  # in_bed, efficiency, awake, core, deep, rem

        by_ts = {r["feature_type"]: r["date"] for r in rows}
        # Session-level rows anchor at the session end (F6).
        assert by_ts["sleep_in_bed"] == "2026-08-11T06:00:00"
        assert by_ts["sleep_efficiency"] == "2026-08-11T06:00:00"
        # Per-interval stage rows anchor at THEIR OWN end time, not the
        # session start/end (F6).
        assert by_ts["sleep_awake"] == "2026-08-10T22:10:00"
        assert by_ts["sleep_core"] == "2026-08-11T01:10:00"
        assert by_ts["sleep_deep"] == "2026-08-11T02:40:00"
        assert by_ts["sleep_rem"] == "2026-08-11T04:00:00"

    def test_multiple_intervals_of_same_stage_each_get_their_own_row(self):
        # Two separate deep-sleep cycles must NOT be summed into one row —
        # each interval is its own row at its own end time (matches HiMe's
        # native per-segment storage), so they naturally land on distinct
        # (timestamp, feature_type) keys instead of colliding.
        session = self._session(sleep_stage_intervals=[
            {"stage": "deep", "start_time": "2026-08-10T23:00:00Z", "end_time": "2026-08-10T23:30:00Z"},
            {"stage": "deep", "start_time": "2026-08-11T02:00:00Z", "end_time": "2026-08-11T02:20:00Z"},
        ])
        rows = mapper.map_sleep_session(session)
        deep_rows = [r for r in rows if r["feature_type"] == "sleep_deep"]
        assert len(deep_rows) == 2
        by_ts = {r["date"]: r["value"] for r in deep_rows}
        assert by_ts["2026-08-10T23:30:00"] == 30 * 60.0
        assert by_ts["2026-08-11T02:20:00"] == 20 * 60.0

    def test_falls_back_to_stage_summary_without_intervals(self):
        session = self._session(stages={
            "awake_minutes": 20, "light_minutes": 180, "deep_minutes": 90, "rem_minutes": 70,
        })
        rows = mapper.map_sleep_session(session)
        by_feature = {r["feature_type"]: r["value"] for r in rows}
        assert "sleep_asleep" not in by_feature  # staged (via summary) -> no total row (F5)
        assert by_feature["sleep_awake"] == 20 * 60.0
        assert by_feature["sleep_core"] == 180 * 60.0
        assert by_feature["sleep_deep"] == 90 * 60.0
        assert by_feature["sleep_rem"] == 70 * 60.0
        # Anchored at session end, not start (F6).
        assert all(r["date"] == "2026-08-11T06:00:00" for r in rows)

    def test_unstaged_session_emits_total_only(self):
        # No sleep_stage_intervals AND no `stages` breakdown at all.
        session = self._session()
        rows = mapper.map_sleep_session(session)
        by_feature = {r["feature_type"]: r["value"] for r in rows}
        assert by_feature["sleep_asleep"] == 7 * 3600
        assert "sleep_core" not in by_feature
        assert "sleep_deep" not in by_feature
        assert "sleep_rem" not in by_feature
        assert all(r["date"] == "2026-08-11T06:00:00" for r in rows)  # anchored at session end (F6)

    def test_is_nap_recorded_in_metadata(self):
        session = self._session(is_nap=True, stages={"deep_minutes": 20})
        rows = mapper.map_sleep_session(session)
        assert rows
        meta = json.loads(rows[0]["metadata"])
        assert meta["is_nap"] is True

    def test_provider_filtering(self):
        session = self._session(source={"provider": "apple", "device": "watch"})
        assert mapper.map_sleep_session(session) == []

    def test_missing_end_time_skipped(self):
        session = self._session(end_time=None)
        assert mapper.map_sleep_session(session) == []

    def test_empty_session(self):
        assert mapper.map_sleep_session({}) == []
        assert mapper.map_sleep_session(None) == []


# ---------------------------------------------------------------------------
# Workout mapping
# ---------------------------------------------------------------------------


class TestWorkoutMapping:
    def _workout(self, **overrides):
        workout = {
            "id": "w-1",
            "type": "running",
            "name": "Morning Run",
            "start_time": "2026-08-10T07:00:00Z",
            "end_time": "2026-08-10T07:30:00Z",
            "zone_offset": "+00:00",
            "duration_seconds": 1800,
            "source": {"provider": "garmin", "device": "forerunner"},
            "calories_kcal": 300.0,
            "distance_meters": 5000.0,
            "avg_heart_rate_bpm": 150,
            "max_heart_rate_bpm": 175,
        }
        workout.update(overrides)
        return workout

    def test_known_category_emits_all_three_metrics(self):
        rows = mapper.map_workout(self._workout())
        by_feature = {r["feature_type"]: r["value"] for r in rows}
        assert by_feature["workout_running_duration"] == 1800.0
        assert by_feature["workout_running_distance"] == 5000.0
        assert by_feature["workout_running_energy"] == 300.0

    def test_metadata_carries_hr_and_details(self):
        rows = mapper.map_workout(self._workout())
        meta = json.loads(rows[0]["metadata"])
        assert meta["workout_type"] == "running"
        assert meta["avg_heart_rate_bpm"] == 150
        assert meta["max_heart_rate_bpm"] == 175
        assert meta["name"] == "Morning Run"

    def test_category_without_distance_field_skips_it(self):
        # "yoga" has no workout_yoga_distance in FEATURE_SPEC -> falls back to
        # workout_other_distance (which DOES exist), never silently dropped.
        rows = mapper.map_workout(self._workout(type="yoga", duration_seconds=1200, distance_meters=10.0))
        features = {r["feature_type"] for r in rows}
        assert "workout_yoga_duration" in features
        assert "workout_other_distance" in features  # yoga has no native distance feature

    def test_unknown_type_falls_back_to_other(self):
        rows = mapper.map_workout(self._workout(type="curling", distance_meters=None))
        features = {r["feature_type"] for r in rows}
        assert features == {"workout_other_duration", "workout_other_energy"}

    def test_duration_derived_from_start_end_when_missing(self):
        workout = self._workout(duration_seconds=None)
        rows = mapper.map_workout(workout)
        by_feature = {r["feature_type"]: r["value"] for r in rows}
        assert by_feature["workout_running_duration"] == 1800.0

    def test_provider_filtering(self):
        rows = mapper.map_workout(self._workout(source={"provider": "apple_health", "device": "watch"}))
        assert rows == []

    def test_empty_workout(self):
        assert mapper.map_workout({}) == []
        assert mapper.map_workout(None) == []
