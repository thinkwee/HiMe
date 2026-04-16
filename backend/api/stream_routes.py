"""
Streaming routes for real-time data and agent output via WebSocket.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.connection_manager import agent_monitor_manager, data_stream_manager
from ..services.streaming_service import DataStreamingService
from .config_routes import get_app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["streaming"])


@router.websocket("/data")
async def stream_data(websocket: WebSocket):
    """
    Stream wearable data via WebSocket.

    Reads configuration from app state and streams data.
    """
    logger.info("=== WebSocket connection attempt ===")
    await data_stream_manager.connect(websocket)

    try:
        logger.info("Starting data stream...")
        # Single-user mode — always LiveUser
        app_state = get_app_state()
        pids = ['LiveUser']
        stream_config = app_state.get('stream_config', {}).copy()

        logger.info(f"Stream config: {stream_config}")

        # Use DataStreamingService with the connection manager's set
        await DataStreamingService.stream_data(
            websocket, pids, stream_config, data_stream_manager.active_connections
        )

    except WebSocketDisconnect:
        logger.info("Data stream WebSocket disconnected")
    except Exception as e:
        logger.error(f"Error in data stream: {e}", exc_info=True)
        try:
            await websocket.send_json({
                'type': 'error',
                'error': str(e)
            })
        except Exception as send_err:
            logger.debug("Failed to send error to WebSocket: %s", send_err)
    finally:
        data_stream_manager.disconnect(websocket)
        from .config_routes import save_app_state
        await asyncio.to_thread(save_app_state)


@router.websocket("/agent/{user_id}")
async def monitor_autonomous_agent(websocket: WebSocket, user_id: str):
    """
    Monitor an autonomous agent via WebSocket (optional).

    Streams agent events (code execution, results, errors) and status updates.
    If WebSocket disconnects, agent continues running.
    """
    await agent_monitor_manager.connect(websocket)

    try:
        from .agent_state import active_agents

        agent_info = active_agents.get(user_id)
        if not agent_info:
            await websocket.send_json({
                'type': 'error',
                'error': f'No active agent for {user_id}. Start it first with POST /api/agent/start'
            })
            return

        event_queue = agent_info.get('event_queue')

        await websocket.send_json({
            'type': 'monitor_connected',
            'user_id': user_id,
            'message': 'Connected. Streaming agent activity...'
        })

        # Use a lock to prevent concurrent websocket sends
        _ws_lock = asyncio.Lock()
        _stop = asyncio.Event()

        async def _event_pusher():
            """Dedicated task: drain event queue and push to WebSocket immediately."""
            while not _stop.is_set():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.3)
                    batch = [event]
                    # Drain any additional queued events without waiting
                    while len(batch) < 50:
                        try:
                            batch.append(event_queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    async with _ws_lock:
                        for ev in batch:
                            try:
                                safe_ev = json.loads(json.dumps(ev, default=str))
                                await websocket.send_json(safe_ev)
                            except Exception:
                                continue
                except asyncio.TimeoutError:
                    continue
                except (WebSocketDisconnect, ConnectionError, RuntimeError):
                    break
                except Exception:
                    logger.error("Unexpected error in event pusher", exc_info=True)
                    break

        async def _status_pusher():
            """Dedicated task: periodic status updates, independent of event flow."""
            while not _stop.is_set():
                await asyncio.sleep(3.0)
                if _stop.is_set():
                    break
                try:
                    # During startup, agent may not be ready yet — skip status push
                    current_info = active_agents.get(user_id)
                    agent = current_info and current_info.get('agent')
                    if not agent or current_info.get('_starting'):
                        continue
                    status = await asyncio.to_thread(agent.get_status)
                    payload = {
                        'type': 'status_update',
                        'status': status,
                        'data_store_stats': status.get('data_store_stats', {}),
                    }
                    safe_payload = json.loads(json.dumps(payload, default=str))
                    async with _ws_lock:
                        await websocket.send_json(safe_payload)
                except (WebSocketDisconnect, ConnectionError, RuntimeError):
                    break
                except Exception:
                    logger.error("Unexpected error in status pusher", exc_info=True)
                    break

        pusher_task = asyncio.create_task(_event_pusher())
        status_task = asyncio.create_task(_status_pusher())
        try:
            # Wait until either task exits (connection lost)
            done, _ = await asyncio.wait(
                [pusher_task, status_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            _stop.set()
            pusher_task.cancel()
            status_task.cancel()


    except WebSocketDisconnect:
        logger.info(f"Monitor disconnected for {user_id} (agent continues running)")
    except Exception as e:
        logger.error(f"Monitor error: {e}")
    finally:
        agent_monitor_manager.disconnect(websocket)


@router.get("/connections")
async def get_active_connections():
    """Get number of active WebSocket connections."""
    return {
        'success': True,
        'data_stream_active_connections': data_stream_manager.count(),
        'agent_monitor_active_connections': agent_monitor_manager.count()
    }
