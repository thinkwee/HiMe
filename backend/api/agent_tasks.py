"""
Scheduled task CRUD endpoints, trigger rules CRUD, and manual analysis trigger.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .agent_state import _get_or_create_memory, active_agents

logger = logging.getLogger(__name__)

tasks_router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScheduledTaskCreate(BaseModel):
    cron_expr: str
    prompt_goal: str


class ScheduledTaskUpdate(BaseModel):
    cron_expr: str | None = None
    prompt_goal: str | None = None
    status: str | None = None  # "active" | "paused" | "deleted"


# ---------------------------------------------------------------------------
# GET /scheduled-tasks/{user_id}
# ---------------------------------------------------------------------------

@tasks_router.get("/scheduled-tasks/{user_id}")
async def list_scheduled_tasks(user_id: str):
    """List all scheduled tasks for a user."""
    memory = _get_or_create_memory(user_id)
    if not memory:
        return {"success": True, "tasks": []}
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, cron_expr, prompt_goal, status, last_run_at, created_at "
                "FROM scheduled_tasks WHERE status != 'deleted' ORDER BY id"
            ).fetchall()
        return {"success": True, "tasks": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /scheduled-tasks/{user_id}
# ---------------------------------------------------------------------------

@tasks_router.post("/scheduled-tasks/{user_id}")
async def create_scheduled_task(user_id: str, body: ScheduledTaskCreate):
    """Create a new scheduled task."""
    memory = _get_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    try:
        # Validate cron expression
        import croniter as _croniter
        _croniter.croniter(body.cron_expr)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {body.cron_expr}")
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            cur = conn.execute(
                "INSERT INTO scheduled_tasks (cron_expr, prompt_goal, status) VALUES (?, ?, 'active')",
                (body.cron_expr, body.prompt_goal),
            )
            conn.commit()
            return {"success": True, "id": cur.lastrowid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# PUT /scheduled-tasks/{user_id}/{task_id}
# ---------------------------------------------------------------------------

@tasks_router.put("/scheduled-tasks/{user_id}/{task_id}")
async def update_scheduled_task(user_id: str, task_id: int, body: ScheduledTaskUpdate):
    """Update a scheduled task (change cron, goal, or status)."""
    memory = _get_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    _ALLOWED_FIELDS = {"cron_expr", "prompt_goal", "status"}
    # Validate specific fields before building the update
    if body.cron_expr is not None:
        try:
            import croniter as _croniter
            _croniter.croniter(body.cron_expr)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {body.cron_expr}")
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
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            conn.execute(f"UPDATE scheduled_tasks SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /trigger-analysis/{user_id}
# ---------------------------------------------------------------------------

@tasks_router.post("/trigger-analysis/{user_id}")
async def trigger_analysis(user_id: str, request: Request):
    """Manually trigger an analysis task."""
    info = active_agents.get(user_id)
    if not info:
        raise HTTPException(status_code=404, detail="Agent not running")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")
    goal = body.get("goal", None)
    if not goal or not isinstance(goal, str):
        raise HTTPException(status_code=400, detail="goal must be a non-empty string")
    await info["agent"].run_scheduled_analysis(goal)
    return {"success": True, "message": "Analysis queued"}


# ===========================================================================
# Trigger rules CRUD
# ===========================================================================

class TriggerRuleCreate(BaseModel):
    name: str
    feature_type: str
    condition: str  # gt, lt, gte, lte, avg_gt, avg_lt, spike, drop, delta_gt, absent
    threshold: float
    window_minutes: int = 60
    cooldown_minutes: int = 30
    prompt_goal: str


class TriggerRuleUpdate(BaseModel):
    name: str | None = None
    feature_type: str | None = None
    condition: str | None = None
    threshold: float | None = None
    window_minutes: int | None = None
    cooldown_minutes: int | None = None
    prompt_goal: str | None = None
    status: str | None = None  # "active" | "paused" | "deleted"


_VALID_CONDITIONS = {"gt", "lt", "gte", "lte", "avg_gt", "avg_lt", "spike", "drop", "delta_gt", "absent"}


@tasks_router.get("/trigger-rules/{user_id}")
async def list_trigger_rules(user_id: str):
    """List all trigger rules for a user."""
    memory = _get_or_create_memory(user_id)
    if not memory:
        return {"success": True, "rules": []}
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trigger_rules WHERE status != 'deleted' ORDER BY id"
            ).fetchall()
        return {"success": True, "rules": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@tasks_router.post("/trigger-rules/{user_id}")
async def create_trigger_rule(user_id: str, body: TriggerRuleCreate):
    """Create a new trigger rule."""
    memory = _get_or_create_memory(user_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Participant not found")
    if body.condition not in _VALID_CONDITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid condition '{body.condition}'. Must be one of: {sorted(_VALID_CONDITIONS)}",
        )
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            cur = conn.execute(
                "INSERT INTO trigger_rules (name, feature_type, condition, threshold, window_minutes, cooldown_minutes, prompt_goal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (body.name, body.feature_type, body.condition, body.threshold,
                 body.window_minutes, body.cooldown_minutes, body.prompt_goal),
            )
            conn.commit()
            return {"success": True, "id": cur.lastrowid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@tasks_router.put("/trigger-rules/{user_id}/{rule_id}")
async def update_trigger_rule(user_id: str, rule_id: int, body: TriggerRuleUpdate):
    """Update a trigger rule."""
    memory = _get_or_create_memory(user_id)
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
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            conn.execute(f"UPDATE trigger_rules SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
