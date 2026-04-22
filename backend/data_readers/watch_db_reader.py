"""
Reader for the raw Apple Watch SQLite database written by WatchExporter
(``ios/Server/watch.db``, EAV schema ``health_samples_eav``).

This is the *upstream* reader used only by ``_live_ingest_loop`` in
``backend/api/agent_lifecycle.py``. The application-facing reader for
both the API and the agent is :class:`DataStoreReader`, which reads the
agent's normalized ``samples`` table from ``data/data_stores/{pid}_data.db``.

Uses a single persistent connection to avoid EMFILE (too many open files)
from frequent connect/disconnect.
"""
import errno
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd

from .base_reader import BaseDataReader

logger = logging.getLogger(__name__)

# Retry config for transient DB open failures (e.g. sandbox, lock contention)
_CONNECT_RETRIES = 3
_CONNECT_RETRY_DELAY = 0.5

# Throttle full diagnostics to avoid log spam when polling fails repeatedly
_last_diag_time: float = 0
_DIAG_THROTTLE_SEC = 60


def _log_connect_diagnostics(path: str, exc: Exception) -> None:
    """Log full diagnostics when connection fails, for debugging."""
    global _last_diag_time
    now = time.time()
    if now - _last_diag_time < _DIAG_THROTTLE_SEC:
        logger.error("WATCH_DB_READER: Connection failed (diagnostics throttled): %s", exc)
        return
    _last_diag_time = now

    lines = []
    path_obj = Path(path)
    dir_path = path_obj.parent
    lines.append(f"DB path: {path}")
    lines.append(f"  exists: {path_obj.exists()}")
    if path_obj.exists():
        try:
            st = path_obj.stat()
            lines.append(f"  mode: {oct(st.st_mode)} readable: {os.access(path, os.R_OK)}")
        except OSError as e:
            lines.append(f"  stat failed: {e} (errno={e.errno} {errno.errorcode.get(e.errno, '?')})")
        try:
            with open(path, "rb") as f:
                f.read(1)
            lines.append("  open(path,'rb'): OK")
        except OSError as e:
            lines.append(f"  open(path,'rb') failed: errno={e.errno} {errno.errorcode.get(e.errno, '?')} {e}")
    else:
        lines.append(f"  dir exists: {dir_path.exists()}")
    if dir_path.exists():
        try:
            lines.append(f"  dir readable: {os.access(str(dir_path), os.R_OK)}")
            lines.append(f"  dir writable: {os.access(str(dir_path), os.W_OK)}")
        except OSError as e:
            lines.append(f"  dir access failed: {e} (errno={e.errno})")
    # WAL sidecar files
    for ext in ["-wal", "-shm"]:
        p = Path(str(path) + ext)
        lines.append(f"  {p.name}: exists={p.exists()}")
    # SQLite error details (Python 3.11+)
    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)
    if code is not None:
        lines.append(f"  sqlite_errorcode: {code}")
    if name is not None:
        lines.append(f"  sqlite_errorname: {name}")
    lines.append(f"  exception: {exc!r}")
    logger.error("WATCH_DB_READER: Connection diagnostics:\n%s", "\n".join(lines))


class WatchDBReader(BaseDataReader):
    """
    Reads raw HealthKit samples from the WatchExporter ``watch.db`` (EAV).

    Used by the live ingest loop, not by the API/agent path.
    Uses a single persistent connection to avoid EMFILE from frequent
    connect/disconnect.
    """

    def __init__(self, data_path: Path):
        self.data_path = Path(data_path)
        if not self.data_path.exists():
            self.data_path.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_path / "watch.db"
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        super().__init__(self.data_path)
        logger.info(f"WATCH_DB_READER: Initialized at {self.db_path}")


    def get_available_users(self, datasets: list[str] | None = None) -> list[str]:
        """REQUIRED: Returns the virtual LiveUser."""
        return ["LiveUser"]

    def _get_conn(self) -> sqlite3.Connection:
        """Return shared connection, creating it if needed. Caller must hold _conn_lock."""
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1")
                return self._conn
            except sqlite3.Error:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

        abs_path = str(self.db_path.absolute())
        last_err = None
        for attempt in range(_CONNECT_RETRIES):
            try:
                con = sqlite3.connect(abs_path, timeout=20, check_same_thread=False)
                self._conn = con
                return con
            except sqlite3.Error as e:
                last_err = e
                if attempt < _CONNECT_RETRIES - 1:
                    time.sleep(_CONNECT_RETRY_DELAY * (attempt + 1))
                    continue
        _log_connect_diagnostics(abs_path, last_err)
        raise last_err

    def _execute(self, fn):
        """Run fn(con) with the shared connection. Returns fn result or raises."""
        if not self.db_path.exists():
            raise ValueError("DB does not exist")
        with self._conn_lock:
            con = self._get_conn()
            try:
                return fn(con)
            except sqlite3.Error:
                try:
                    if self._conn:
                        self._conn.close()
                except Exception:
                    pass
                self._conn = None
                raise

    def get_feature_types(self) -> list[str]:
        """REQUIRED: Returns features currently in the DB."""
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
        if not self.db_path.exists():
            logger.warning("watch.db not found at %s, returning defaults", self.db_path)
            return default_features
        try:
            def _run(con):
                rows = con.execute("SELECT DISTINCT f FROM health_samples_eav").fetchall()
                return [r[0] for r in rows] if rows else default_features
            return self._execute(_run)
        except Exception as e:
            logger.warning(f"WATCH_DB_READER: Error getting features: {e}")
            return default_features

    def get_feature_columns(self, feature_type: str) -> list[str]:
        """REQUIRED: Live data always uses a single 'value' column."""
        return ["value"]

    def get_date_range(self, pids: list[str]) -> tuple:
        """REQUIRED: Returns the time window available in DB."""
        if not self.db_path.exists():
            logger.warning("watch.db not found at %s, returning defaults", self.db_path)
            now = pd.Timestamp.now()
            return (now - pd.Timedelta(hours=1), now)
        try:
            def _run(con):
                row = con.execute("SELECT MIN(ts), MAX(ts) FROM health_samples_eav").fetchone()
                if row and row[0] is not None:
                    return (pd.to_datetime(row[0], unit='s', utc=True, errors='coerce'), pd.to_datetime(row[1], unit='s', utc=True, errors='coerce'))
                return None
            out = self._execute(_run)
            if out is not None:
                return out
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
        """REQUIRED: Main data loading method."""
        if not self.db_path.exists():
            logger.warning("watch.db not found at %s, returning defaults", self.db_path)
            return pd.DataFrame()

        minutes = kwargs.get('minutes', 60)
        since_ts = kwargs.get('since_ts')
        since = since_ts if since_ts is not None else (pd.Timestamp.now().timestamp() - (minutes * 60))

        try:
            def _run(con):
                query = "SELECT ts, v FROM health_samples_eav WHERE f = ? AND ts > ? ORDER BY ts"
                return pd.read_sql_query(query, con, params=(feature_type, since))

            df = self._execute(_run)
            if df.empty:
                return pd.DataFrame()
            df['date'] = pd.to_datetime(df['ts'], unit='s', utc=True, errors='coerce')
            bad_rows = df['date'].isna().sum()
            if bad_rows > 0:
                logger.debug("WATCH_DB_READER: Dropped %d rows with unparseable timestamps for %s", bad_rows, feature_type)
                df = df.dropna(subset=['date'])
            if df.empty:
                return pd.DataFrame()
            df['value'] = df['v']
            df['feature_type'] = feature_type
            df['pid'] = "LiveUser"
            # Return 'ts' as well so streaming service can use it for high-water marking
            return df[['date', 'value', 'feature_type', 'pid', 'ts']]
        except Exception as e:
            logger.warning(f"WATCH_DB_READER: Load error for {feature_type}: {e}")
            return pd.DataFrame()

    def get_latest_samples(self) -> dict:
        """EXTRA: Polling helper for legacy use or quick status check."""
        if not self.db_path.exists():
            logger.warning("watch.db not found at %s, returning defaults", self.db_path)
            return {}
        result = {}
        try:
            def _run(con):
                query = """
                SELECT f, ts, v FROM (
                    SELECT f, ts, v, ROW_NUMBER() OVER(PARTITION BY f ORDER BY ts DESC) as rn
                    FROM health_samples_eav
                ) WHERE rn = 1
                """
                return con.execute(query).fetchall()

            rows = self._execute(_run)
            for f, ts, v in rows:
                result[f] = {"ts": ts, "value": v}
        except Exception as e:
            logger.warning(f"WATCH_DB_READER: get_latest_samples error: {e}")
        return result

    def get_all_samples_since(self, since_ts: float) -> list[dict]:
        """EXTRA: Fetch ALL records from watch.db since a specific high-water mark."""
        if not self.db_path.exists():
            logger.warning("watch.db not found at %s, returning defaults", self.db_path)
            return []
        try:
            def _run(con):
                query = "SELECT f, ts, v FROM health_samples_eav WHERE ts > ? ORDER BY ts ASC"
                return con.execute(query, (since_ts,)).fetchall()

            rows = self._execute(_run)
            return [{"feature_type": f, "ts": ts, "value": v} for f, ts, v in rows]
        except Exception as e:
            logger.warning(f"WATCH_DB_READER: get_all_samples_since error: {e}")
            return []

    def get_all_samples_since_id(self, since_id: int, limit: int = 100000) -> list[dict]:
        """Fetch records from watch.db with ID > since_id.

        Args:
            since_id: Fetch records with ID strictly greater than this value.
            limit: Maximum number of rows to return (default 100000).
        """
        if not self.db_path.exists():
            logger.warning("watch.db not found at %s, returning defaults", self.db_path)
            return []
        try:
            def _run(con):
                query = "SELECT id, f, ts, v FROM health_samples_eav WHERE id > ? ORDER BY id ASC LIMIT ?"
                return con.execute(query, (since_id, limit)).fetchall()

            rows = self._execute(_run)
            return [{"id": row[0], "feature_type": row[1], "ts": row[2], "value": row[3]} for row in rows]
        except Exception as e:
            logger.warning(f"WATCH_DB_READER: get_all_samples_since_id error: {e}")
            return []

    def get_samples_updated_since(self, since_updated_at: float, limit: int = 100000) -> list[dict]:
        """Fetch records inserted or modified since the given updated_at timestamp.

        This catches both new inserts and in-place updates (e.g. cumulative
        metric buckets whose value increased after the initial send).

        Args:
            since_updated_at: Epoch float — only rows with updated_at > this value.
            limit: Maximum number of rows to return.
        """
        if not self.db_path.exists():
            return []
        try:
            def _run(con):
                # Check if updated_at column exists (migration may not have run yet)
                cols = [r[1] for r in con.execute("PRAGMA table_info(health_samples_eav)").fetchall()]
                if "updated_at" not in cols:
                    return []
                query = (
                    "SELECT id, f, ts, v, updated_at "
                    "FROM health_samples_eav "
                    "WHERE updated_at > ? "
                    "ORDER BY updated_at ASC LIMIT ?"
                )
                return con.execute(query, (since_updated_at, limit)).fetchall()

            rows = self._execute(_run)
            return [
                {"id": r[0], "feature_type": r[1], "ts": r[2], "value": r[3], "updated_at": r[4]}
                for r in rows
            ]
        except Exception as e:
            logger.warning("WATCH_DB_READER: get_samples_updated_since error: %s", e)
            return []

    def get_all_samples(self, limit: int = 100000) -> list[dict]:
        """Fetch records from watch.db (full historical sync).

        Args:
            limit: Maximum number of rows to return (default 100000).
        """
        return self.get_all_samples_since_id(0, limit=limit)

    def get_total_sample_count(self) -> int:
        """Return the total number of rows in health_samples_eav."""
        if not self.db_path.exists():
            return 0
        try:
            def _run(con):
                return con.execute("SELECT COUNT(*) FROM health_samples_eav").fetchone()
            row = self._execute(_run)
            return int(row[0]) if row else 0
        except Exception as e:
            logger.warning("WATCH_DB_READER: get_total_sample_count error: %s", e)
            return 0
