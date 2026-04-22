"""
SQL query result LRU cache with TTL-based expiration.

Caches SELECT query results to avoid redundant database hits during
analysis cycles. Write operations automatically invalidate affected
cache entries.
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict


class QueryCache:
    """LRU cache for SQL query results with TTL expiration.

    Thread-safe for read-only use (single async event loop).
    Write invalidation clears relevant entries.
    """

    def __init__(self, max_size: int = 50, ttl_seconds: float = 120.0) -> None:
        self._cache: OrderedDict[str, tuple[float, str, dict]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _make_key(self, query: str, database: str) -> str:
        """Normalize query and create a cache key."""
        normalized = " ".join(query.split()).upper()
        return hashlib.md5(f"{database}:{normalized}".encode()).hexdigest()

    def get(self, query: str, database: str) -> dict | None:
        """Look up a cached result. Returns None on miss or expiry."""
        key = self._make_key(query, database)
        if key in self._cache:
            ts, db, result = self._cache[key]
            if time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(key)
                self._hits += 1
                return result
            else:
                del self._cache[key]
        self._misses += 1
        return None

    def put(self, query: str, database: str, result: dict) -> None:
        """Cache a successful query result."""
        if not result.get("success", True):
            return  # Don't cache failures

        key = self._make_key(query, database)
        self._cache[key] = (time.monotonic(), database, result)
        self._cache.move_to_end(key)

        # Evict oldest entries if over capacity
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def invalidate(self, database: str = "") -> None:
        """Invalidate cache entries for a specific database.

        Called after write operations to prevent stale reads.
        If database is empty, clears entire cache.
        """
        if not database:
            self._cache.clear()
            return

        to_delete = [
            k for k, (_, db, _) in self._cache.items()
            if db == database
        ]
        for k in to_delete:
            del self._cache[k]

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total:.1%}" if total > 0 else "N/A",
        }
