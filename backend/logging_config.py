"""
Centralized logging configuration.

All structured Python logs go to stdout only.
The start.sh script redirects stdout → logs/backend.log,
so there is a single log file with no duplicate writes.
"""
import logging
import os
import sys


def setup_logging(level=logging.INFO):
    """Setup logging for the entire application (stdout only).

    The log level can be overridden via the ``LOG_LEVEL`` environment variable
    (DEBUG, INFO, WARNING, ERROR).  The *level* argument is used as the fallback
    when the env var is not set.
    """
    env_level = os.environ.get("LOG_LEVEL", "").upper()
    if env_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = getattr(logging, env_level)

    formatter = logging.Formatter(
        '%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Root logger — clear any existing handlers to prevent duplicates
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    # Console handler (start.sh redirects this to logs/backend.log)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    # Silence noisy libraries
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger('watchfiles').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    return root_logger
