"""Safe resolution of agent-supplied local file paths (charts / images).

The LLM can name any path in ``reply_user(image_path=...)`` or in a report's
``![](...)`` / ``image_paths``.  Without a guard that is an arbitrary local
file read: the file would be uploaded to a messaging platform or embedded in a
report.  ``resolve_chart_path`` is the single gate both tools go through.

A path is accepted only if, after resolving symlinks (strict -- it must
exist), it

* lives under an allowed chart root (the system temp dir; that is where the
  ``code`` tool is told to ``savefig``),
* is a regular file within the size cap,
* has an image extension, and
* really starts with image magic bytes (png / jpeg / gif / webp).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
DEFAULT_MAX_IMAGE_BYTES = 10_000_000


class ChartPathError(ValueError):
    """Raised when a path is not a safe, in-bounds image file."""


def allowed_chart_roots() -> list[Path]:
    """Directories charts may be read from (resolved, de-duplicated)."""
    candidates = [tempfile.gettempdir(), "/tmp"]
    roots: list[Path] = []
    for c in candidates:
        try:
            r = Path(c).resolve()
        except OSError:
            continue
        if r not in roots:
            roots.append(r)
    return roots


def _sniff_image(head: bytes) -> bool:
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head[:6] in (b"GIF87a", b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def resolve_chart_path(
    path: str,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    roots: list[Path] | None = None,
) -> Path:
    """Return the resolved, validated image path or raise ``ChartPathError``.

    Blocking (stat + a 12-byte read) -- call via ``asyncio.to_thread`` from
    async code. Use the *returned* path, not the original string, so a symlink
    swapped in afterwards cannot redirect the read.
    """
    if not path or not isinstance(path, str):
        raise ChartPathError("empty path")
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ChartPathError(f"not found: {path}") from exc

    allowed = roots if roots is not None else allowed_chart_roots()
    if not any(resolved == r or r in resolved.parents for r in allowed):
        raise ChartPathError("outside the allowed chart directory")
    if resolved.suffix.lower() not in IMAGE_EXTS:
        raise ChartPathError("not an image file extension")
    if not resolved.is_file():
        raise ChartPathError("not a regular file")
    try:
        size = resolved.stat().st_size
        if size > max_bytes:
            raise ChartPathError(f"image too large ({size} > {max_bytes} bytes)")
        with open(resolved, "rb") as f:
            head = f.read(12)
    except OSError as exc:
        raise ChartPathError(f"unreadable: {exc}") from exc
    if not _sniff_image(head):
        raise ChartPathError("file content is not a PNG/JPEG/GIF/WebP image")
    return resolved
