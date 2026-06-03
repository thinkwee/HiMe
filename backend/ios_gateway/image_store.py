"""Short-lived registry mapping opaque image IDs to local files.

The agent saves charts to ``/tmp`` (via the code tool); ``IOSGateway.send_photo``
registers the path here and emits a ``chat_image`` event carrying an authed
URL (``/api/agent/chat-image/<id>``). The fetch endpoint serves the file only
to the owning user, so the raw filesystem path is never exposed. Entries
expire so references to stale ``/tmp`` files (which the OS may reap) don't
accumulate.
"""
from __future__ import annotations

import threading
import time
import uuid

_TTL_S = 3600.0  # references live for one hour


class ImageStore:
    """In-memory ``image_id`` → ``(user_id, path, expiry)`` map."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def register(self, user_id: str, path: str) -> str:
        """Register *path* for *user_id* and return an opaque image id."""
        image_id = uuid.uuid4().hex
        with self._lock:
            self._gc_locked()
            self._items[image_id] = (user_id, path, time.monotonic() + _TTL_S)
        return image_id

    def get(self, image_id: str, user_id: str) -> str | None:
        """Return the path for *image_id* iff owned by *user_id* and fresh."""
        with self._lock:
            item = self._items.get(image_id)
            if item is None:
                return None
            owner, path, expiry = item
            if owner != user_id:
                return None  # per-user authorization
            if time.monotonic() > expiry:
                self._items.pop(image_id, None)
                return None
            return path

    def _gc_locked(self) -> None:
        now = time.monotonic()
        for k in [k for k, (_, _, exp) in self._items.items() if now > exp]:
            self._items.pop(k, None)


# Process-global singleton shared by IOSGateway (writer) and the fetch route.
image_store = ImageStore()
