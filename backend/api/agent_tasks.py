"""
Scheduled task CRUD endpoints, trigger rules CRUD, and manual analysis trigger.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..config import settings
from .agent_state import _check_rate_limit, _client_ip, active_agents, aget_or_create_memory

logger = logging.getLogger(__name__)

tasks_router = APIRouter()

_TRIGGER_ANALYSIS_MAX_CALLS = 10  # per minute per IP


def _validate_cron(expr: str) -> None:
    """Raise HTTP 400 unless *expr* is a cron expression that can actually fire.

    ``croniter(expr)`` alone accepts impossible schedules such as
    ``0 0 31 2 *`` (Feb 31st) and only fails when asked for the next run.
    """
    import croniter as _croniter
    try:
        _croniter.croniter(expr, datetime.now(timezone.utc)).get_next(datetime)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {expr}") from None


def _non_empty(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("must not be empty")
    return v


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScheduledTaskCreate(BaseModel):
    cron_expr: str
    prompt_goal: str

    check_goal = field_validator("prompt_goal")(_non_empty)


class ScheduledTaskUpdate(BaseModel):
    cron_expr: str | None = None
    prompt_goal: str | None = None
    status: str | None = None  # "active" | "paused" | "deleted"

    check_goal = field_validator("prompt_goal")(_non_empty)


# ---------------------------------------------------------------------------
# GET /scheduled-tasks/{user_id}
# ---------------------------------------------------------------------------

@tasks_router.get("/scheduled-tasks/{user_id}")
async def list_scheduled_tasks(user_id: str):
    """List all scheduled tasks for a user.

    Includes ``timezone`` so the dashboard can label cron expressions with
    the wall-clock the server actually uses.
    """
    memory = await aget_or_create_memory(user_id)
    if not memory:
        return {"success": True, "tasks": [], "timezone": settings.TIMEZONE}
    def _query() -> list[dict]:
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, cron_expr, prompt_goal, status, last_run_at, created_at "
                "FROM scheduled_tasks WHERE status != 'deleted' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    try:
        tasks = await asyncio.to_thread(_query)
        return {
            "success": True,
            "tasks": tasks,
            "timezone": settings.TIMEZONE,
        }
    except Exception as e:
        logger.error("Failed to list scheduled tasks for %s: %s", user_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list scheduled tasks.") from e


# ---------------------------------------------------------------------------
# POST /scheduled-tasks/{user_id}
# ---------------------------------------------------------------------------

@tasks_router.post("/scheduled-tasks/{user_id}")
async def create_scheduled_task(user_id: str, body: ScheduledTaskCreate):
    """Create a new scheduled task."""
    memory = await aget_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    _validate_cron(body.cron_expr)

    def _insert() -> int:
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            cur = conn.execute(
                "INSERT INTO scheduled_tasks (cron_expr, prompt_goal, status) VALUES (?, ?, 'active')",
                (body.cron_expr, body.prompt_goal),
            )
            conn.commit()
            return cur.lastrowid

    try:
        new_id = await asyncio.to_thread(_insert)
        return {"success": True, "id": new_id}
    except Exception as e:
        logger.error("Failed to create scheduled task for %s: %s", user_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create scheduled task.") from e


# ---------------------------------------------------------------------------
# PUT /scheduled-tasks/{user_id}/{task_id}
# ---------------------------------------------------------------------------

@tasks_router.put("/scheduled-tasks/{user_id}/{task_id}")
async def update_scheduled_task(user_id: str, task_id: int, body: ScheduledTaskUpdate):
    """Update a scheduled task (change cron, goal, or status)."""
    memory = await aget_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    _ALLOWED_FIELDS = {"cron_expr", "prompt_goal", "status"}
    # Validate specific fields before building the update
    if body.cron_expr is not None:
        _validate_cron(body.cron_expr)
    if body.status is not None:
        if body.status not in ("active", "paused", "deleted"):
            raise HTTPException(status_code=400, detail="status must be active, paused, or deleted")
    updates = []
    params: list = []
    for field in _ALLOWED_FIELDS:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if not updates:
        return {"success": True, "message": "Nothing to update"}
    params.append(task_id)

    def _update() -> int:
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            cur = conn.execute(
                f"UPDATE scheduled_tasks SET {', '.join(updates)} WHERE id = ?", params
            )
            conn.commit()
            return cur.rowcount

    try:
        rowcount = await asyncio.to_thread(_update)
    except Exception as e:
        logger.error("Failed to update scheduled task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update scheduled task.") from e
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id} not found")
    return {"success": True}


# ---------------------------------------------------------------------------
# POST /trigger-analysis/{user_id}
# ---------------------------------------------------------------------------

@tasks_router.post("/trigger-analysis/{user_id}")
async def trigger_analysis(user_id: str, request: Request):
    """Manually trigger an analysis task."""
    _check_rate_limit(_client_ip(request), "trigger-analysis", _TRIGGER_ANALYSIS_MAX_CALLS)
    info = active_agents.get(user_id)
    if not info:
        raise HTTPException(status_code=404, detail="Agent not running")
    agent = info.get("agent")
    if agent is None:
        raise HTTPException(status_code=409, detail="Agent is still starting up")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")
    goal = body.get("goal", None) if isinstance(body, dict) else None
    if not goal or not isinstance(goal, str) or not goal.strip():
        raise HTTPException(status_code=400, detail="goal must be a non-empty string")
    # Cap the backlog at the API boundary (the queue itself is bounded, but
    # an explicit 429 beats silently dropping the request).
    queue = getattr(agent, "_analysis_queue", None)
    if queue is not None and queue.maxsize > 0 and queue.qsize() >= queue.maxsize:
        raise HTTPException(status_code=429, detail="Analysis queue is full; try again later.")
    await agent.run_scheduled_analysis(goal)
    return {"success": True, "message": "Analysis queued"}


# ===========================================================================
# Trigger rules CRUD
# ===========================================================================

class TriggerRuleCreate(BaseModel):
    name: str = Field(min_length=1)
    feature_type: str = Field(min_length=1)
    condition: str  # gt, lt, gte, lte, avg_gt, avg_lt, spike, drop, delta_gt, absent
    threshold: float = Field(allow_inf_nan=False)
    window_minutes: int = Field(default=60, gt=0)
    cooldown_minutes: int = Field(default=30, ge=0)
    prompt_goal: str

    check_goal = field_validator("prompt_goal")(_non_empty)


class TriggerRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    feature_type: str | None = Field(default=None, min_length=1)
    condition: str | None = None
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    window_minutes: int | None = Field(default=None, gt=0)
    cooldown_minutes: int | None = Field(default=None, ge=0)
    prompt_goal: str | None = None
    status: str | None = None  # "active" | "paused" | "deleted"

    check_goal = field_validator("prompt_goal")(_non_empty)


_VALID_CONDITIONS = {"gt", "lt", "gte", "lte", "avg_gt", "avg_lt", "spike", "drop", "delta_gt", "absent"}


@tasks_router.get("/trigger-rules/{user_id}")
async def list_trigger_rules(user_id: str):
    """List all trigger rules for a user."""
    memory = await aget_or_create_memory(user_id)
    if not memory:
        return {"success": True, "rules": []}
    def _query() -> list[dict]:
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trigger_rules WHERE status != 'deleted' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    try:
        return {"success": True, "rules": await asyncio.to_thread(_query)}
    except Exception as e:
        logger.error("Failed to list trigger rules for %s: %s", user_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list trigger rules.") from e


@tasks_router.post("/trigger-rules/{user_id}")
async def create_trigger_rule(user_id: str, body: TriggerRuleCreate):
    """Create a new trigger rule."""
    memory = await aget_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    if body.condition not in _VALID_CONDITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid condition '{body.condition}'. Must be one of: {sorted(_VALID_CONDITIONS)}",
        )
    def _insert() -> int:
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            cur = conn.execute(
                "INSERT INTO trigger_rules (name, feature_type, condition, threshold, window_minutes, cooldown_minutes, prompt_goal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (body.name, body.feature_type, body.condition, body.threshold,
                 body.window_minutes, body.cooldown_minutes, body.prompt_goal),
            )
            conn.commit()
            return cur.lastrowid

    try:
        return {"success": True, "id": await asyncio.to_thread(_insert)}
    except Exception as e:
        logger.error("Failed to create trigger rule for %s: %s", user_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create trigger rule.") from e


@tasks_router.put("/trigger-rules/{user_id}/{rule_id}")
async def update_trigger_rule(user_id: str, rule_id: int, body: TriggerRuleUpdate):
    """Update a trigger rule."""
    memory = await aget_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    if body.condition is not None and body.condition not in _VALID_CONDITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid condition '{body.condition}'. Must be one of: {sorted(_VALID_CONDITIONS)}",
        )
    if body.status is not None and body.status not in ("active", "paused", "deleted"):
        raise HTTPException(status_code=400, detail="status must be active, paused, or deleted")

    _ALLOWED_RULE_FIELDS = {"name", "feature_type", "condition", "threshold", "window_minutes", "cooldown_minutes", "prompt_goal", "status"}
    updates = []
    params: list = []
    for field in _ALLOWED_RULE_FIELDS:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if not updates:
        return {"success": True, "message": "Nothing to update"}
    params.append(rule_id)

    def _update() -> int:
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            cur = conn.execute(
                f"UPDATE trigger_rules SET {', '.join(updates)} WHERE id = ?", params
            )
            conn.commit()
            return cur.rowcount

    try:
        rowcount = await asyncio.to_thread(_update)
    except Exception as e:
        logger.error("Failed to update trigger rule %s: %s", rule_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update trigger rule.") from e
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Trigger rule {rule_id} not found")
    return {"success": True}
