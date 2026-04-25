from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backend import utils
from backend.config import settings


def test_ts_fmt_normalizes_to_utc() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = datetime(2026, 1, 2, 11, 4, 5, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert utils.ts_fmt(naive) == "2026-01-02T03:04:05"
    assert utils.ts_fmt(aware) == "2026-01-02T03:04:05"


def test_parse_db_iso_utc_handles_valid_invalid_and_empty() -> None:
    assert utils.parse_db_iso_utc(None) is None
    assert utils.parse_db_iso_utc("") is None
    assert utils.parse_db_iso_utc("not-a-timestamp") is None

    parsed_naive = utils.parse_db_iso_utc("2026-01-02T03:04:05")
    assert parsed_naive == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    parsed_aware = utils.parse_db_iso_utc("2026-01-02T11:04:05+08:00")
    assert parsed_aware == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_app_timezone_uses_config_and_falls_back_to_utc(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Shanghai")
    assert utils.app_timezone().key == "Asia/Shanghai"

    monkeypatch.setattr(settings, "TIMEZONE", "Invalid/Timezone")
    assert utils.app_timezone().key == "UTC"


def test_now_helpers_are_timezone_aware(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TIMEZONE", "Europe/London")

    assert utils.now_utc().tzinfo == timezone.utc
    assert utils.now_local().tzinfo is not None


def test_dataframe_to_json_safe_cleans_non_serializable_values() -> None:
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-02T03:04:05", None], utc=True),
            "value": [1.5, np.inf],
            "raw": [np.nan, "ok"],
            "object_dt": [pd.Timestamp("2026-01-03T04:05:06Z"), pd.NA],
        }
    )

    result = utils.dataframe_to_json_safe(df)

    assert result == [
        {
            "timestamp": "2026-01-02T03:04:05",
            "value": 1.5,
            "raw": None,
            "object_dt": "2026-01-03T04:05:06",
        },
        {
            "timestamp": None,
            "value": None,
            "raw": "ok",
            "object_dt": None,
        },
    ]


def test_dataframe_to_json_safe_empty_dataframe() -> None:
    assert utils.dataframe_to_json_safe(pd.DataFrame()) == []


def test_serialize_value_handles_numpy_datetime_scalars_and_arrays() -> None:
    assert utils.serialize_value(np.int64(7)) == 7
    assert utils.serialize_value(np.float64(np.inf)) is None
    assert utils.serialize_value(np.array([1, 2, 3])) == [1, 2, 3]
    assert utils.serialize_value(pd.Timestamp("2026-01-02T03:04:05Z")) == "2026-01-02T03:04:05"
    assert utils.serialize_value(np.datetime64("2026-01-02T03:04:05")) == "2026-01-02T03:04:05"


def test_clean_dict_for_json_recursively_serializes_nested_data() -> None:
    data = {
        "top": np.float64(1.25),
        "nested": {
            "when": pd.Timestamp("2026-01-02T03:04:05Z"),
            "missing": np.nan,
        },
        "items": [np.int64(2), np.float64(np.nan), np.array([3, 4])],
    }

    cleaned = utils.clean_dict_for_json(data)

    assert cleaned == {
        "top": 1.25,
        "nested": {
            "when": "2026-01-02T03:04:05",
            "missing": None,
        },
        "items": [2, None, [3, 4]],
    }
