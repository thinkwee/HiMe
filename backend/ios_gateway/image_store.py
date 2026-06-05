"""Registry mapping opaque image IDs to durable, owner-scoped files.

The agent saves charts to ``/tmp`` (via the code tool). ``IOSGateway.send_photo``
calls :meth:`ImageStore.register`, which copies the chart into the user's
durable ``<DATA_STORE_PATH>/<uid>/chat_images/`` dir and returns an opaque image
id. The gateway emits a ``chat_image`` event carrying an authed URL
(``/api/agent/chat-image/<id>``) and persists the id on the assistant's
``chat_history`` turn.

Why a durable copy (not the raw ``/tmp`` path): the in-memory map below is lost
on restart and its entries expire after an hour, and the OS may reap ``/tmp``.
Without a durable copy the image would only ever render live and vanish on the
next app open or backend restart. The fetch route resolves the id via
:meth:`get` (fast, in-proc) and falls back to :meth:`durable_path` (filesystem)
so replayed history keeps working forever.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_TTL_S = 3600.0  # in-memory references live for one hour (then served via disk)
# Durable charts older than this are reaped on the next register() so the
# per-user dir can't grow without bound. Comfortably longer than the
# chat_history retention window so a replayable turn never loses its image.
_DURABLE_MAX_AGE_S = 90 * 24 * 3600.0


def _chat_images_dir(user_id: str) -> Path:
    from ..config import settings
    return Path(settings.DATA_STORE_PATH) / user_id / "chat_images"


class ImageStore:
    """In-memory ``image_id`` → ``(user_id, path, expiry)`` map over durable files."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def register(self, user_id: str, path: str) -> str:
        """Persist *path* into *user_id*'s durable dir and return an opaque id.

        Falls back to referencing the original path if the durable copy fails,
        so a transient FS error still delivers the image live.
        """
        image_id = uuid.uuid4().hex
        durable = self._persist(user_id, image_id, path)
        stored = durable or path
        with self._lock:
            self._gc_locked()
            self._items[image_id] = (user_id, stored, time.monotonic() + _TTL_S)
        return image_id

    def _persist(self, user_id: str, image_id: str, src: str) -> str | None:
        try:
            ext = os.path.splitext(src)[1].lower() or ".png"
            dest_dir = _chat_images_dir(user_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            self._reap_old(dest_dir)
            dest = dest_dir / f"{image_id}{ext}"
            shutil.copyfile(src, dest)
            return str(dest)
        except Exception as e:  # pragma: no cover — FS/permission errors
            logger.warning("chat-image persist failed for user=%s: %s", user_id, e)
            return None

    @staticmethod
    def _reap_old(dest_dir: Path) -> None:
        try:
            now = time.time()
            for f in dest_dir.iterdir():
                try:
                    if now - f.stat().st_mtime > _DURABLE_MAX_AGE_S:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass

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

    @staticmethod
    def durable_path(image_id: str, user_id: str) -> str | None:
        """Filesystem fallback: locate a persisted chart by id for its owner.

        Used by the fetch route when the in-memory entry has expired or was
        lost to a restart. ``image_id`` is a hex uuid, so it can't escape the
        user's ``chat_images`` dir via path traversal.
        """
        if not image_id or not image_id.isalnum():
            return None
        try:
            d = _chat_images_dir(user_id)
            for f in d.glob(f"{image_id}.*"):
                if f.is_file():
                    return str(f)
        except Exception:
            pass
        return None

    def _gc_locked(self) -> None:
        now = time.monotonic()
        for k in [k for k, (_, _, exp) in self._items.items() if now > exp]:
            self._items.pop(k, None)


# Process-global singleton shared by IOSGateway (writer) and the fetch route.
image_store = ImageStore()
