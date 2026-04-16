"""
Agent V2 API routes — thin hub that assembles sub-routers.

All business logic lives in:
  - agent_state.py        (shared state, models, helpers)
  - agent_lifecycle.py    (start/stop/status, supervisor, restore, ingestion)
  - agent_diagnostics.py  (activity, memory, tools, telegram-info)
  - agent_tasks.py        (scheduled task CRUD, trigger-analysis)
"""
from __future__ import annotations

from fastapi import APIRouter

from .agent_diagnostics import diagnostics_router
from .agent_lifecycle import (  # noqa: F401  — re-exported for main.py
    lifecycle_router,
    shutdown_agents,
    start_system_ingestion,
    stop_all_ingestions,
    try_restore_agent,
)
from .agent_state import (  # noqa: F401  — re-exported for main.py
    active_agents,
    get_active_agents_dict,
    get_memory_manager_for,
)
from .agent_tasks import tasks_router

router = APIRouter(prefix="/api/agent", tags=["agent"])
router.include_router(lifecycle_router)
router.include_router(diagnostics_router)
router.include_router(tasks_router)
