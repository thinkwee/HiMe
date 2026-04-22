"""
Persistence layer for Autonomous Agent state.
Handles saving and restoring agent context, history, and metadata.
"""
import json
import logging
import os
from pathlib import Path

from ..utils import ts_now

logger = logging.getLogger(__name__)

class AgentStateRepository:
    """
    Repository for persisting agent state to disk.
    Uses JSON files for state storage.
    """

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _get_state_file(self, user_id: str) -> Path:
        return self.storage_dir / f"{user_id}_state.json"

    def save_state(self, user_id: str, state: dict):
        """
        Save full agent state to disk.

        Args:
            user_id: Participant ID
            state: Dictionary containing all state variables (cycle_count, messages, etc.)
        """
        file_path = self._get_state_file(user_id)

        # Work on a shallow copy so we don't mutate the caller's dict
        state_copy = {**state, 'last_updated': ts_now()}

        try:
            temp_path = file_path.with_suffix('.json.tmp')
            with open(temp_path, 'w') as f:
                json.dump(state_copy, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            # Atomic rename (safe across POSIX systems)
            temp_path.replace(file_path)
            logger.debug(f"Saved agent state for {user_id}")
        except Exception as e:
            logger.error(f"Failed to save agent state: {e}")

    def load_state(self, user_id: str) -> dict | None:
        """
        Load agent state from disk.

        Returns:
            State dictionary or None if no state exists.
        """
        file_path = self._get_state_file(user_id)

        if not file_path.exists():
            return None

        try:
            with open(file_path) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load agent state: {e}")
            return None

    def clear_state(self, user_id: str):
        """Delete saved state."""
        file_path = self._get_state_file(user_id)
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Cleared agent state for {user_id}")
