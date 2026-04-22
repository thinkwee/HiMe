"""Shared prompt loading utility.

Loads prompt templates from the ``prompts/`` directory and substitutes
``{placeholders}`` using a safe formatter that leaves unknown keys untouched.
"""
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path("prompts")


def safe_format(template: str, **kwargs: object) -> str:
    """Substitute only known ``{key}`` placeholders; leave all others untouched.

    Handles ``{{`` / ``}}`` escape sequences (same semantics as
    ``str.format()``): they are converted to literal ``{`` / ``}``.
    """
    _L = "\x00L\x00"
    _R = "\x00R\x00"
    result = template.replace("{{", _L).replace("}}", _R)

    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", str(value))

    return result.replace(_L, "{").replace(_R, "}")


def load_prompt(name: str, *, cache: bool = True) -> str:
    """Load a prompt template file from the ``prompts/`` directory.

    Parameters
    ----------
    name:
        File name (e.g. ``"rules_analysis.md"``) relative to ``prompts/``.
    cache:
        If *True*, use an LRU cache for repeated loads.  Agent-writable files
        like ``experience.md`` or ``user.md`` should pass ``cache=False``.

    Returns
    -------
    str
        The raw template text, or ``""`` if the file is missing.
    """
    if cache:
        return _load_cached(name)
    return _load_uncached(name)


@lru_cache(maxsize=32)
def _load_cached(name: str) -> str:
    return _load_uncached(name)


def _load_uncached(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        logger.warning("Prompt file not found: %s", path)
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
        logger.debug("Loaded prompt %s (%d chars)", name, len(text))
        return text
    except Exception as e:
        logger.error("Failed to read prompt %s: %s", name, e)
        return ""


def load_and_format(name: str, *, cache: bool = True, **kwargs: object) -> str:
    """Load a prompt template and substitute ``{placeholders}``."""
    template = load_prompt(name, cache=cache)
    if not template:
        return ""
    return safe_format(template, **kwargs)


def clear_cache() -> None:
    """Clear the prompt file cache (e.g. after hot-reload)."""
    _load_cached.cache_clear()
