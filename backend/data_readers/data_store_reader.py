"""
Reader for the agent's normalized DataStore (``data/data_stores/{pid}_data.db``,
table ``samples``). Both the API layer and the agent's SQL tool read from
this store, so it's the application-facing reader.

The upstream raw watch.db (EAV) reader lives in :mod:`watch_db_reader`.
"""
import logging
import sqlite3
from pathlib import Path

import pandas as pd

from .base_reader import BaseDataReader

logger = logging.getLogger(__name__)

class DataStoreReader(BaseDataReader):
    """
    Reader for the agent's normalized DataStore (``samples`` table).
    Points to ``data/data_stores/{pid}_data.db``.
    """

    def __init__(self, data_path: Path):
        self.data_path = Path(data_path)
        super().__init__(self.data_path)
        logger.info(f"DATA_STORE_READER: Initialized at {self.data_path}")

    def _get_db_path(self, pid: str) -> Path:
        return self.data_path / f"{pid}_data.db"

    def get_available_users(self, datasets: list[str] | None = None) -> list[str]:
        pids = []
        for f in self.data_path.glob("*_data.db"):
            pids.append(f.name.replace("_data.db", ""))
        return sorted(pids) if pids else ["LiveUser"]

    def get_feature_types(self) -> list[str]:
        pids = self.get_available_users()
        default_features = [
            # Original & Core
            "steps", "distance", "flights_climbed", "exercise_time", "stand_time",
            "active_energy", "resting_energy", "water", "heart_rate",
            "resting_heart_rate", "walking_heart_rate_avg", "heart_rate_variability",
            "blood_oxygen", "vo2max", "respiratory_rate", "walking_speed",
            "body_mass", "body_mass_index", "sleeping_wrist_temp",
            "mindful_session", "sleep_in_bed", "sleep_asleep", "sleep_core",
            "sleep_deep", "sleep_rem", "sleep_awake",
            # New Mobility & Heart
            "walking_steadiness", "walking_asymmetry", "walking_step_length",
            "walking_double_support", "six_minute_walk", "stair_ascent_speed",
            "stair_descent_speed", "heart_rate_recovery", "atrial_fibrillation_burden",
            # New Athletics
            "running_power", "running_stride_length", "running_vertical_oscillation",
            "running_ground_contact", "cycling_speed", "cycling_cadence", "cycling_power",
            # New Environment & Alerts
            "time_in_daylight", "uv_index", "audio_exposure_event",
            "high_heart_rate_event", "low_heart_rate_event", "irregular_heart_rhythm_event"
        ]
        if not pids:
            return default_features

        try:
            db_path = self._get_db_path(pids[0])
            with sqlite3.connect(str(db_path), timeout=20, check_same_thread=False) as conn:
                rows = conn.execute("SELECT DISTINCT feature_type FROM samples").fetchall()
            db_features = [r[0] for r in rows] if rows else []
            return sorted(list(set(db_features + default_features)))
        except Exception:
            return default_features

    def get_feature_columns(self, feature_type: str) -> list[str]:
        return ["value"]

    def get_date_range(self, pids: list[str]) -> tuple:
        if not pids:
            return (pd.Timestamp.now(), pd.Timestamp.now())
        try:
            db_path = self._get_db_path(pids[0])
            with sqlite3.connect(str(db_path), timeout=20, check_same_thread=False) as conn:
                row = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM samples").fetchone()
            if row and row[0]:
                return (pd.to_datetime(row[0]), pd.to_datetime(row[1]))
        except Exception:
            pass
        now = pd.Timestamp.now()
        return (now - pd.Timedelta(hours=1), now)

    def load_feature_data(
        self,
        pids: list[str],
        feature_type: str,
        datasets: list[str] | None = None,
        **kwargs
    ) -> pd.DataFrame:
        if not pids:
            return pd.DataFrame()
        pid = pids[0]

        minutes = kwargs.get('minutes', 60)
        since_ts = kwargs.get('since_ts')

        if since_ts:
            from datetime import datetime, timezone

            from ..utils import ts_fmt
            since_dt = ts_fmt(datetime.fromtimestamp(since_ts, tz=timezone.utc))
            query = "SELECT timestamp as date, value FROM samples WHERE feature_type = ? AND timestamp > ? ORDER BY timestamp"
            params = (feature_type, since_dt)
        else:
            from datetime import datetime, timedelta, timezone

            from ..utils import ts_fmt
            now = datetime.now(timezone.utc)
            since_dt = ts_fmt(now - timedelta(minutes=minutes))
            query = "SELECT timestamp as date, value FROM samples WHERE feature_type = ? AND timestamp > ? ORDER BY timestamp"
            params = (feature_type, since_dt)

        try:
            db_path = self._get_db_path(pid)
            with sqlite3.connect(str(db_path), timeout=20, check_same_thread=False) as conn:
                df = pd.read_sql_query(query, conn, params=params)

            if df.empty:
                return pd.DataFrame()

            df['date'] = pd.to_datetime(df['date'], format='mixed', utc=True)
            df['feature_type'] = feature_type
            df['pid'] = pid
            # Convert ISO back to float ts for streaming logic compatibility
            df['ts'] = df['date'].apply(lambda x: x.timestamp())
            return df
        except Exception as e:
            logger.error(f"DATA_STORE_READER: Error loading {feature_type}: {e}")
            return pd.DataFrame()

    def get_total_sample_count(self, pids: list[str] | None = None) -> int:
        """Total row count across all users' samples tables."""
        if pids is None:
            pids = self.get_available_users()
        total = 0
        for pid in pids:
            db_path = self._get_db_path(pid)
            if not db_path.exists():
                continue
            try:
                with sqlite3.connect(str(db_path), timeout=20, check_same_thread=False) as conn:
                    row = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
                if row and row[0] is not None:
                    total += int(row[0])
            except Exception as e:
                logger.warning(f"DATA_STORE_READER: count error for {pid}: {e}")
        return total

    def load_features_batch(
        self,
        pids: list[str],
        feature_types: list[str],
        minutes: int | None = None,
        since_ts: float | None = None,
        max_rows_per_feature: int = 2000,
    ) -> pd.DataFrame:
        """
        Load multiple features in a single query. Reduces DB round-trips from N to 1.

        Args:
            max_rows_per_feature: Cap total rows returned to
                ``max_rows_per_feature * len(feature_types)`` to prevent OOM
                on large date ranges.
        """
        if not pids or not feature_types:
            return pd.DataFrame()
        pid = pids[0]

        from datetime import datetime, timedelta, timezone

        from ..utils import ts_fmt
        if since_ts is not None:
            since_dt = ts_fmt(datetime.fromtimestamp(since_ts, tz=timezone.utc))
        else:
            mins = minutes if minutes is not None else 60
            since_dt = ts_fmt(datetime.now(timezone.utc) - timedelta(minutes=mins))

        placeholders = ",".join("?" * len(feature_types))
        row_limit = max_rows_per_feature * len(feature_types)
        query = (
            f"SELECT timestamp as date, feature_type, value FROM samples "
            f"WHERE feature_type IN ({placeholders}) AND timestamp > ? ORDER BY timestamp LIMIT ?"
        )
        params = (*feature_types, since_dt, row_limit)

        try:
            db_path = self._get_db_path(pid)
            with sqlite3.connect(str(db_path), timeout=20, check_same_thread=False) as conn:
                df = pd.read_sql_query(query, conn, params=params)
            if df.empty:
                return df
            df["date"] = pd.to_datetime(df["date"], format="mixed", utc=True)
            df["pid"] = pid
            df["ts"] = df["date"].apply(lambda x: x.timestamp())
            return df
        except Exception as e:
            logger.error(f"DATA_STORE_READER: Batch load error: {e}")
            return pd.DataFrame()
