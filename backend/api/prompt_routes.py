import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

PROMPTS_DIR = (Path(__file__).resolve().parent.parent.parent / "prompts")

class PromptUpdate(BaseModel):
    content: str

PROMPT_FILES = {
    "soul": {"file": "soul.md", "agent_editable": False, "title": "Soul"},
    "data_schema": {"file": "data_schema.md", "agent_editable": False, "title": "Data Schema"},
    "memory_guide": {"file": "memory_guide.md", "agent_editable": False, "title": "Memory Guide"},
    "create_page_guide": {"file": "create_page_guide.md", "agent_editable": False, "title": "Create Page Guide"},
    "experience": {"file": "experience.md", "agent_editable": True, "title": "Experience"},
    "user": {"file": "user.md", "agent_editable": True, "title": "User"},
}

@router.get("")
async def list_prompts():
    """List all available prompts and their metadata."""
    prompts = []
    for key, info in PROMPT_FILES.items():
        path = PROMPTS_DIR / info["file"]
        content = ""
        if path.exists():
            content = await asyncio.to_thread(path.read_text, "utf-8")

        prompts.append({
            "id": key,
            "title": info["title"],
            "file": info["file"],
            "agent_editable": info["agent_editable"],
            "content": content
        })
    return {"success": True, "prompts": prompts}

@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str):
    """Get content of a specific prompt."""
    if prompt_id not in PROMPT_FILES:
        raise HTTPException(status_code=404, detail="Prompt not found")

    info = PROMPT_FILES[prompt_id]
    path = PROMPTS_DIR / info["file"]

    if not path.exists():
        return {"success": True, "id": prompt_id, "content": ""}

    content = await asyncio.to_thread(path.read_text, "utf-8")
    return {
        "success": True,
        "id": prompt_id,
        "title": info["title"],
        "agent_editable": info["agent_editable"],
        "content": content
    }

@router.post("/{prompt_id}")
async def update_prompt(prompt_id: str, update: PromptUpdate):
    """Update content of a specific prompt and hot-reload live agents."""
    if prompt_id not in PROMPT_FILES:
        raise HTTPException(status_code=404, detail="Prompt not found")

    info = PROMPT_FILES[prompt_id]
    path = PROMPTS_DIR / info["file"]

    try:
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, update.content, "utf-8")
        _hot_reload_prompt_caches()
        logger.info(f"Updated prompt {prompt_id} ({info['file']}) and refreshed caches")
        return {"success": True, "message": f"Prompt '{prompt_id}' updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update prompt {prompt_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _hot_reload_prompt_caches() -> None:
    """Drop cached prompt text so the next agent turn re-reads from disk."""
    try:
        from ..agent.prompt_loader import clear_cache as clear_prompt_cache
        clear_prompt_cache()
    except Exception as e:
        logger.warning("Failed to clear prompt_loader cache: %s", e)

    try:
        from .agent_state import active_agents
        for info in active_agents.values():
            agent = info.get("agent")
            if agent is None:
                continue
            # AgentPromptsMixin caches soul.md on the instance; drop it so the
            # next prompt assembly re-reads the file.
            if hasattr(agent, "_soul_text"):
                try:
                    delattr(agent, "_soul_text")
                except AttributeError:
                    pass
    except Exception as e:
        logger.warning("Failed to invalidate live-agent prompt caches: %s", e)
