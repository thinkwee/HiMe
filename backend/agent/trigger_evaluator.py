"""
Trigger evaluator — monitors incoming health data against user/agent-defined rules.

Evaluates trigger_rules after each data ingestion batch and queues analysis
tasks when conditions are met. Integrates with the existing agent event loop
via agent._analysis_queue.

Supported conditions:
  - gt (greater than)     : latest value > threshold
  - lt (less than)        : latest value < threshold
  - avg_gt / avg_lt       : window average above/below threshold
  - spike                 : latest value > (window_avg + threshold * window_std)
                            (when std=0, uses absolute: latest > avg + threshold)
  - drop                  : latest value < (window_avg - threshold * window_std)
                            (when std=0, uses absolute: latest < avg - threshold)
  - delta_gt              : |latest - previous| > threshold
  - absent                : no data for feature_type in last window_minutes

Cooldown: each rule has a cooldown_minutes field. After triggering, the rule
won't fire again until the cooldown has elapsed.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..utils import ts_fmt, ts_now

logger = logging.getLogger(__name__)

# Conditions that compare a single latest value
_SIMPLE_CONDITIONS = {"gt", "lt", "gte", "lte"}
# Conditions that require a window of data
_WINDOW_CONDITIONS = {"avg_gt", "avg_lt", "spike", "drop", "delta_gt", "absent"}
_ALL_CONDITIONS = _SIMPLE_CONDITIONS | _WINDOW_CONDITIONS


class TriggerEvaluator:
    """Evaluate trigger rules against health data and queue analysis tasks."""

    def __init__(
        self,
        memory_db_path: Path,
        user_id: str,
        health_db_path: Path,
    ) -> None:
        self.memory_db_file = memory_db_path / f"{user_id}.db"
        self.health_db_file = health_db_path
        self.user_id = user_id
        self._last_eval_time: float = 0.0
        # Minimum seconds between full evaluations (prevents spam on rapid ingestion)
        self._min_eval_interval: float = 10.0

    async def evaluate_after_ingest(
        self,
        agent_queue: asyncio.Queue | None = None,
        ingested_features: set | None = None,
    ) -> list[dict[str, Any]]:
        """
        Evaluate all active trigger rules after a data ingestion batch.

        Args:
            agent_queue: The agent's _analysis_queue to push triggered goals into.
            ingested_features: Set of feature_types that were just ingested.
                              If provided, only rules matching these features are checked.

        Returns:
            List of triggered rule dicts (for logging/testing).
        """
        now = time.monotonic()
        if now - self._last_eval_time < self._min_eval_interval:
            return []
        self._last_eval_time = now

        rules = await asyncio.to_thread(self._load_active_rules)
        if not rules:
            return []

        # Filter to only rules that match ingested features (if known)
        if ingested_features:
            rules = [r for r in rules if r["feature_type"] in ingested_features]

        triggered: list[dict[str, Any]] = []
        for rule in rules:
            if self._is_on_cooldown(rule):
                continue
            try:
                fired = await asyncio.to_thread(self._evaluate_rule, rule)
            except Exception as exc:
                logger.debug("Trigger rule %d eval error: %s", rule["id"], exc)
                continue

            if fired:
                triggered.append(rule)
                await asyncio.to_thread(self._mark_triggered, rule["id"])
                goal = (
                    f"[Triggered: {rule['name']}] "
                    f"{rule['prompt_goal']}"
                )
                logger.info(
                    "Trigger fired: rule=%d name='%s' feature=%s condition=%s",
                    rule["id"], rule["name"], rule["feature_type"], rule["condition"],
                )
                if agent_queue is not None:
                    await agent_queue.put(goal)

        return triggered

    # ------------------------------------------------------------------
    # Rule loading
    # ------------------------------------------------------------------

    def _load_active_rules(self) -> list[dict[str, Any]]:
        """Load all active trigger rules from memory DB."""
        try:
            with sqlite3.connect(str(self.memory_db_file), timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM trigger_rules WHERE status = 'active'"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug("Failed to load trigger rules: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Cooldown check
    # ------------------------------------------------------------------

    @staticmethod
    def _is_on_cooldown(rule: dict[str, Any]) -> bool:
        """Check if a rule is still in its cooldown period."""
        last_triggered = rule.get("last_triggered_at")
        if not last_triggered:
            return False
        cooldown_min = rule.get("cooldown_minutes", 30)
        try:
            last_dt = datetime.strptime(last_triggered[:19], "%Y-%m-%dT%H:%M:%S")
            last_dt = last_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - last_dt) < timedelta(minutes=cooldown_min)
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Rule evaluation (runs in thread pool)
    # ------------------------------------------------------------------

    def _evaluate_rule(self, rule: dict[str, Any]) -> bool:
        """Evaluate a single rule against current health data. Returns True if triggered."""
        condition = rule["condition"]
        feature = rule["feature_type"]
        threshold = rule["threshold"]
        window_min = rule.get("window_minutes", 60)

        if condition not in _ALL_CONDITIONS:
            logger.warning("Unknown trigger condition: %s", condition)
            return False

        with sqlite3.connect(str(self.health_db_file), timeout=10) as conn:
            # Get latest value
            latest_row = conn.execute(
                "SELECT value, timestamp FROM samples "
                "WHERE feature_type = ? ORDER BY timestamp DESC LIMIT 1",
                (feature,),
            ).fetchone()

            if latest_row is None:
                return condition == "absent"

            latest_value = latest_row[0]

            # Simple threshold conditions
            if condition == "gt":
                return latest_value > threshold
            elif condition == "lt":
                return latest_value < threshold
            elif condition == "gte":
                return latest_value >= threshold
            elif condition == "lte":
                return latest_value <= threshold

            # Window-based conditions
            since = ts_fmt(datetime.now(timezone.utc) - timedelta(minutes=window_min))
            window_rows = conn.execute(
                "SELECT value FROM samples "
                "WHERE feature_type = ? AND timestamp > ? ORDER BY timestamp",
                (feature, since),
            ).fetchall()

            if condition == "absent":
                return len(window_rows) == 0

            values = [r[0] for r in window_rows if r[0] is not None]

            # avg_gt/avg_lt work with 1+ values; spike/drop/delta_gt need 2+
            if not values:
                return False
            if condition in ("spike", "drop", "delta_gt") and len(values) < 2:
                return False

            avg = statistics.mean(values)
            std = statistics.stdev(values) if len(values) >= 2 else 0.0

            if condition == "avg_gt":
                return avg > threshold
            elif condition == "avg_lt":
                return avg < threshold
            elif condition == "spike":
                # When std == 0 (flatline), treat threshold as absolute units
                bound = avg + threshold * std if std > 0 else avg + threshold
                return latest_value > bound
            elif condition == "drop":
                # When std == 0 (flatline), treat threshold as absolute units
                bound = avg - threshold * std if std > 0 else avg - threshold
                return latest_value < bound
            elif condition == "delta_gt":
                previous = values[-2]
                return abs(latest_value - previous) > threshold

        return False

    # ------------------------------------------------------------------
    # Mark triggered
    # ------------------------------------------------------------------

    def _mark_triggered(self, rule_id: int) -> None:
        """Update the rule's last_triggered_at and increment trigger_count."""
        try:
            with sqlite3.connect(str(self.memory_db_file), timeout=10) as conn:
                conn.execute(
                    "UPDATE trigger_rules SET last_triggered_at = ?, trigger_count = trigger_count + 1 WHERE id = ?",
                    (ts_now(), rule_id),
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Failed to mark trigger rule %d: %s", rule_id, exc)


def insert_default_trigger_rules(memory_db_file: Path) -> None:
    """Insert sensible default trigger rules if the table is empty."""
    try:
        with sqlite3.connect(str(memory_db_file), timeout=10) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trigger_rules").fetchone()[0]
            if count > 0:
                return

            defaults = [
                (
                    "High resting heart rate",
                    "resting_heart_rate",
                    "gt",
                    85.0,
                    60,
                    120,
                    "Resting heart rate exceeded 85 BPM. Investigate recent cardiovascular data, check for stress signals, and assess whether this is exercise-related or concerning.",
                ),
                (
                    "Low resting heart rate",
                    "resting_heart_rate",
                    "lt",
                    45.0,
                    60,
                    120,
                    "Resting heart rate dropped below 45 BPM. Investigate recent cardiovascular data and assess whether this reflects good fitness or a concerning bradycardia.",
                ),
                (
                    "Low blood oxygen",
                    "blood_oxygen",
                    "lt",
                    0.93,
                    30,
                    180,
                    "Blood oxygen dropped below 93%. Analyze respiratory data, check for breathing disturbances, and assess severity.",
                ),
            ]

            conn.executemany(
                "INSERT INTO trigger_rules (name, feature_type, condition, threshold, window_minutes, cooldown_minutes, prompt_goal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                defaults,
            )
            conn.commit()
            logger.info("Inserted %d default trigger rules", len(defaults))
    except Exception as exc:
        logger.debug("Failed to insert default trigger rules: %s", exc)
