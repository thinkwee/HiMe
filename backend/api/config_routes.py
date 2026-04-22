"""
Configuration routes for stream settings.
Single-user mode — always uses "LiveUser", no user selection needed.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["configuration"])


# Global state (persisted to disk)
_app_state_lock = asyncio.Lock()
app_state = {
    'selected_users': ['LiveUser'],
    'stream_config': {
        'granularity': 'real-time'
    },
    'is_streaming': False,
    'current_data_timestamp': None,
    'live_history_window': '1hour',
}


def save_app_state():
    """Save app_state to disk."""
    try:
        settings.APP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.APP_STATE_PATH, 'w') as f:
            json.dump(app_state, f, indent=2)
        logger.debug(f"App state saved to {settings.APP_STATE_PATH}")
    except Exception as e:
        logger.error(f"Failed to save app state: {e}")


def load_app_state():
    """Load app_state from disk."""
    global app_state
    if settings.APP_STATE_PATH.exists():
        try:
            with open(settings.APP_STATE_PATH) as f:
                loaded = json.load(f)
                app_state.update(loaded)
            # Always ensure LiveUser is the user
            app_state['selected_users'] = ['LiveUser']
            logger.info(f"App state loaded from {settings.APP_STATE_PATH}")
        except Exception as e:
            logger.error(f"Failed to load app state: {e}")


class StreamConfig(BaseModel):
    """Configuration for stream settings."""
    granularity: str = "real-time"
    is_streaming: bool | None = None
    live_history_window: str | None = None


@router.post("/users")
async def configure_users(config: dict | None = None):
    """No-op — single-user mode, always uses LiveUser."""
    return {
        'success': True,
        'users': ['LiveUser'],
        'count': 1
    }


@router.get("/users")
async def get_user_config():
    """Always returns LiveUser."""
    return {
        'users': ['LiveUser'],
    }


@router.post("/stream")
async def configure_stream(config: StreamConfig):
    """Configure stream settings."""
    try:
        async with _app_state_lock:
            app_state['stream_config'] = {
                'granularity': 'real-time'
            }

            if config.is_streaming is not None:
                app_state['is_streaming'] = config.is_streaming
                logger.info(f"Streaming status: {config.is_streaming}")

            if config.live_history_window:
                app_state['live_history_window'] = config.live_history_window
                logger.info(f"Live history window: {config.live_history_window}")

            await asyncio.to_thread(save_app_state)

        return {
            'success': True,
            'config': app_state['stream_config'],
            'is_streaming': app_state['is_streaming']
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error configuring stream: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stream")
async def get_stream_config():
    """Get current stream configuration."""
    return {
        'granularity': 'real-time',
        'is_streaming': app_state.get('is_streaming', False),
        'selected_users': ['LiveUser'],
        'live_history_window': app_state.get('live_history_window', '1hour')
    }


def get_app_state():
    """Get application state."""
    return app_state


@router.get("/defaults")
async def get_defaults():
    """Return backend default LLM provider, model, and data source from .env."""
    from ..agent.llm_providers import _DEFAULT_MODELS
    from ..config import settings

    return {
        "llm_provider": settings.DEFAULT_LLM_PROVIDER,
        "model": settings.DEFAULT_MODEL,
        "data_source": "live",
        "provider_models": {k.value: v for k, v in _DEFAULT_MODELS.items()}
    }
