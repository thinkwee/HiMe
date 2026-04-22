"""
Data store for continuous health data ingestion.
Separates data ingestion from agent analysis.
"""
import asyncio
import logging
import re
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Matches ISO-8601 timestamps: YYYY-MM-DD(T| )HH:MM...
_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')


class DataStore:
    """
    Manages health data storage and continuous ingestion.
    Agent queries this store, doesn't receive pushed batches.
    """

    def __init__(self, db_path: Path, user_id: str):
        """
        Initialize data store.

        Args:
            db_path: Path to database directory
            user_id: Participant ID
        """
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.user_id = user_id
        self.db_file = self.db_path / f"{user_id}_data.db"

        self._init_database()
        self.is_ingesting = False

        logger.info(f"DataStore initialized: {self.db_file}")

    def _init_database(self):
        """Create database schema."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()

            # Main health data table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS samples (
                    timestamp TEXT NOT NULL,
                    feature_type TEXT NOT NULL,
                    value REAL,
                    metadata TEXT,
                    ingested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                    PRIMARY KEY (timestamp, feature_type)
                )
            """)

            # Metadata table for store state (e.g. ingestion progress)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
                )
            """)

            # Index for fast queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_samples_timestamp
                ON samples(timestamp)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_samples_feature
                ON samples(feature_type)
            """)

            # Composite index for batch queries that filter by feature + time
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_samples_feature_timestamp
                ON samples(feature_type, timestamp)
            """)

            conn.commit()

            # One-time migration: add updated_at column (existing databases)
            try:
                has_updated_at = conn.execute(
                    "SELECT 1 FROM pragma_table_info('samples') WHERE name='updated_at'"
                ).fetchone()
                if not has_updated_at:
                    # ALTER TABLE ADD COLUMN requires a constant default
                    conn.execute(
                        "ALTER TABLE samples ADD COLUMN updated_at TEXT"
                    )
                    # Backfill: copy ingested_at into updated_at for existing rows
                    conn.execute(
                        "UPDATE samples SET updated_at = ingested_at WHERE updated_at IS NULL"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_samples_updated_at "
                        "ON samples(updated_at)"
                    )
                    conn.commit()
                    logger.info("Migrated samples table: added updated_at column")
            except Exception as e:
                logger.debug("updated_at migration skipped: %s", e)

            # One-time migration: rename legacy 'health_data' table → 'samples'
            try:
                has_legacy = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='health_data'"
                ).fetchone()
                if has_legacy:
                    conn.execute("ALTER TABLE health_data RENAME TO samples")
                    # Drop old indexes (will be re-created above on next init)
                    for idx in ("idx_health_data_timestamp", "idx_health_data_feature"):
                        conn.execute(f"DROP INDEX IF EXISTS {idx}")
                    conn.commit()
                    logger.info("Migrated table health_data → samples")
            except Exception as e:
                logger.debug("Table rename migration skipped: %s", e)

            # One-time migration: normalize timestamps to YYYY-MM-DDTHH:MM:SS
            # (strip microseconds and timezone suffix from older data)
            try:
                needs = conn.execute(
                    "SELECT 1 FROM samples WHERE length(timestamp) > 19 LIMIT 1"
                ).fetchone()
                if needs:
                    # Remove rows that would collide after truncation
                    conn.execute("""
                        DELETE FROM samples WHERE rowid NOT IN (
                            SELECT MIN(rowid) FROM samples
                            GROUP BY substr(timestamp, 1, 19), feature_type
                        )
                    """)
                    conn.execute(
                        "UPDATE samples SET timestamp = substr(timestamp, 1, 19) "
                        "WHERE length(timestamp) > 19"
                    )
                    conn.execute(
                        "UPDATE samples SET ingested_at = substr(ingested_at, 1, 19) "
                        "WHERE ingested_at IS NOT NULL AND length(ingested_at) > 19"
                    )
                    conn.commit()
                    logger.info("Migrated timestamps to seconds precision")
            except Exception as e:
                logger.debug("Timestamp migration skipped: %s", e)

    def get_connection(self):
        """Get database connection for agent queries."""
        return sqlite3.connect(self.db_file, timeout=30)

    def save_ingestion_progress(self, timestamp: str):
        """Save the timestamp of the last ingested data batch."""
        if not timestamp:
            return
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO store_metadata (key, value, updated_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
                ('last_ingested_timestamp', timestamp)
            )
            conn.commit()

    def get_last_ingestion_time(self) -> str | None:
        """Get the timestamp of the last ingested data batch."""
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM store_metadata WHERE key = 'last_ingested_timestamp'")
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def get_last_ingested_id(self) -> int:
        """Get the ID of the last ingested record from the source (if applicable)."""
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM store_metadata WHERE key = 'last_ingested_id'")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def save_ingestion_id(self, source_id: int):
        """Save the ID of the last ingested record."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO store_metadata (key, value, updated_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
                ('last_ingested_id', str(source_id))
            )
            conn.commit()

    def get_last_updated_at(self) -> float:
        """Get the updated_at high-water mark (epoch float) for watch.db polling."""
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                row = conn.execute(
                    "SELECT value FROM store_metadata WHERE key = 'last_updated_at'"
                ).fetchone()
                return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    def save_last_updated_at(self, updated_at: float) -> None:
        """Save the updated_at high-water mark (epoch float)."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO store_metadata (key, value, updated_at) "
                "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
                ('last_updated_at', str(updated_at)),
            )
            conn.commit()

    def ingest_batch(self, batch: dict):
        """
        Ingest a data batch into the store.
        Uses executemany for batch inserts to minimize lock hold time.

        Args:
            batch: Batch dictionary with 'data' key containing records
        """
        data_records = batch.get('data', [])
        data_timestamp = batch.get('data_timestamp')

        # Save progress even if batch is empty (to advance time)
        if data_timestamp:
            self.save_ingestion_progress(data_timestamp)

        if not data_records:
            return

        rows: list[tuple] = []
        for record in data_records:
            timestamp = record.get('date') or record.get('timestamp')
            if not timestamp:
                continue

            if not _ISO_RE.match(str(timestamp)):
                logger.warning("Skipping record with invalid timestamp: %s", timestamp)
                continue

            if 'feature_type' in record:
                feature_type = record['feature_type']
                value = record.get('value')
                if value is None:
                    continue
                rows.append((timestamp, feature_type, value, None))
            else:
                metadata_suffixes = ('__device', '__source', '__unit')
                for key, val in record.items():
                    if key in ('date', 'timestamp', 'pid', 'dataset'):
                        continue
                    if any(key.endswith(suffix) for suffix in metadata_suffixes):
                        continue
                    if val is None or not isinstance(val, (int, float)):
                        continue
                    rows.append((timestamp, key, val, None))

        if not rows:
            return

        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.executemany(
                """
                INSERT INTO samples
                (timestamp, feature_type, value, metadata, updated_at)
                VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))
                ON CONFLICT(timestamp, feature_type) DO UPDATE SET
                    value = excluded.value,
                    metadata = excluded.metadata,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')
                WHERE excluded.value != samples.value
                """,
                rows,
            )
            conn.commit()


    async def ingest_from_stream(self, stream_iterator: AsyncIterator[dict]):
        """
        Continuously ingest data from a stream.
        Runs as a background task.
        Uses thread pool for ingest_batch to avoid blocking the event loop
        (large batches with many SQLite INSERTs would otherwise stall the stream).
        """
        self.is_ingesting = True

        try:
            async for batch in stream_iterator:
                if not self.is_ingesting:
                    break

                num_records = len(batch.get('data', []))
                if num_records > 0:
                    # Run blocking SQLite writes in thread pool to avoid blocking event loop
                    await asyncio.to_thread(self.ingest_batch, batch)
                    logger.info(f"Ingested batch: {num_records} records (window: {batch.get('data_timestamp', '?')})")

        except Exception as e:
            logger.error(f"Ingestion error: {e}", exc_info=True)
        finally:
            self.is_ingesting = False
            logger.info("Data ingestion stopped")

    def stop_ingestion(self):
        """Stop data ingestion."""
        self.is_ingesting = False

    def query(self, sql: str) -> pd.DataFrame:
        """
        Query data store.

        Args:
            sql: SQL query

        Returns:
            DataFrame with results
        """
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            return pd.read_sql(sql, conn)

    def get_stats(self) -> dict:
        """Get data store statistics."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            cursor = conn.cursor()

            # Total records
            cursor.execute("SELECT COUNT(*) FROM samples")
            total_records = cursor.fetchone()[0]

            # By feature type
            cursor.execute("""
                SELECT feature_type, COUNT(*) as count
                FROM samples
                GROUP BY feature_type
            """)
            by_feature = {row[0]: row[1] for row in cursor.fetchall()}

            # Time range (handle empty table)
            cursor.execute("""
                SELECT MIN(timestamp), MAX(timestamp)
                FROM samples
            """)
            row = cursor.fetchone()
            min_time = row[0] if row else None
            max_time = row[1] if row else None

            return {
                'total_records': total_records,
                'by_feature': by_feature,
                'time_range': {
                    'min': min_time,
                    'max': max_time
                }
            }
