"""
Data readers for wearable health data.

Two readers exist; choose by what you actually want to read:

- :class:`WatchDBReader` — raw HealthKit samples from
  ``ios/Server/watch.db`` (EAV schema). Used by the ingest loop only.
- :class:`DataStoreReader` — normalized ``samples`` table from
  ``data/data_stores/{pid}_data.db``. Used by the API and the agent.

The :func:`create_reader` factory currently always returns a
:class:`DataStoreReader` (the application-facing path). The unused
historical ``data_path`` argument is accepted but ignored — the reader
always points at :data:`settings.DATA_STORE_PATH`.
"""
import logging
from pathlib import Path

from .base_reader import BaseDataReader
from .data_store_reader import DataStoreReader
from .watch_db_reader import WatchDBReader

logger = logging.getLogger(__name__)

__all__ = [
    'BaseDataReader',
    'WatchDBReader',
    'DataStoreReader',
    'create_reader',
]


def create_reader(
    data_source: str = "datastore",
    data_path: Path | None = None,
    **kwargs,
) -> BaseDataReader:
    """Factory for the application-facing data reader.

    Args:
        data_source: Logical source name. Supported values:

            - ``"datastore"`` (preferred) — the agent's normalized DataStore.
            - ``"live"`` — legacy alias kept for backward compatibility.
              Returns the same :class:`DataStoreReader`.
        data_path: Currently ignored. The reader always points at
            :data:`backend.config.settings.DATA_STORE_PATH`. The argument
            exists so older callers don't break.

    Returns:
        Initialized :class:`DataStoreReader` instance.

    Raises:
        ValueError: If ``data_source`` is unrecognised.
    """
    src = data_source.lower()
    if src in ("datastore", "live"):
        from ..config import settings
        logger.info("Creating DataStoreReader for: %s", settings.DATA_STORE_PATH)
        return DataStoreReader(settings.DATA_STORE_PATH)

    raise ValueError(
        f"Unsupported data source: {data_source}. "
        f"Use 'datastore' (or legacy alias 'live')."
    )
