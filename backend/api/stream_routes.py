"""
Streaming routes for real-time data and agent output via WebSocket.
"""
import asyncio
import json
import logging
import secrets
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..ios_gateway.connections import ios_connections
from ..services.connection_manager import agent_monitor_manager, data_stream_manager
from ..services.streaming_service import DataStreamingService
from .config_routes import get_app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["streaming"])


def _ws_token_ok(websocket: WebSocket) -> bool:
    """Validate the bearer token for a WebSocket handshake.

    ``BearerAuthMiddleware`` subclasses Starlette's ``BaseHTTPMiddleware``,
    whose ``__call__`` short-circuits every non-``http`` ASGI scope — so it
    never runs for ``websocket`` connections. WS routes must therefore enforce
    the token themselves or they are wide open whenever ``API_AUTH_TOKEN`` is
    set. Mirrors the middleware: no token configured → open; otherwise accept
    it from ``?token=`` (browsers can't set headers on a WS handshake) or an
    ``Authorization: Bearer`` header.
    """
    token = settings.API_AUTH_TOKEN
    if not token:
        return True
    provided: str | None = websocket.query_params.get("token")
    if not provided:
        auth = websocket.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth.split(" ", 1)[1].strip()
    if not provided:
        return False
    # Constant-time compare so the token can't be recovered byte-by-byte.
    return secrets.compare_digest(provided, token)


@router.websocket("/data")
async def stream_data(websocket: WebSocket):
    """
    Stream wearable data via WebSocket.

    Reads configuration from app state and streams data.
    """
    logger.info("=== WebSocket connection attempt ===")
    if not _ws_token_ok(websocket):
        await websocket.close(code=1008)  # policy violation
        return
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
        from .config_routes import save_app_state_locked
        await save_app_state_locked()


# Heartbeat contract for the agent stream (client=ios):
#   client -> {"type":"ping"}  every ~20s (and once right after connecting)
#   server -> {"type":"pong"}
# Any client frame refreshes presence. Once a connection has sent at least one
# frame it must keep talking: IOS_IDLE_TIMEOUT_S of silence closes the socket
# and unregisters presence. Legacy clients that never send anything keep the
# old "online while the handler runs" semantics.
IOS_IDLE_TIMEOUT_S = 60.0
AGENT_WAIT_POLL_S = 1.0     # how often a waiting iOS socket re-checks for the agent
LEGACY_KEEPALIVE_S = 15.0   # iOS builds that never send frames: liveness probe
STATUS_INTERVAL_S = 3.0     # web-monitor status_update cadence

_DISCONNECT_ERRORS = (WebSocketDisconnect, ConnectionError, RuntimeError)


@router.websocket("/agent/{user_id}")
async def monitor_autonomous_agent(websocket: WebSocket, user_id: str):
    """
    Monitor an autonomous agent via WebSocket.

    Streams agent events (code execution, results, errors). Web clients also
    get periodic ``status_update`` frames; iOS (``?client=ios``) does not.
    If the WebSocket disconnects, the agent continues running.

    iOS-only behaviour: if no agent is active yet the socket stays open and the
    server sends ``{"type":"agent_waiting"}``, then streams normally once the
    agent appears (and returns to waiting if it is stopped). Web clients still
    get the legacy ``error`` frame and close.
    """
    if not _ws_token_ok(websocket):
        await websocket.close(code=1008)  # policy violation
        return
    await agent_monitor_manager.connect(websocket)

    # Track iOS app presence (drives the WS-vs-APNs delivery decision in
    # IOSGateway). Only the iOS client counts — a web dashboard monitor must
    # not suppress push notifications to the phone.
    _is_ios = websocket.query_params.get("client") == "ios"
    _conn_id = uuid.uuid4().hex
    if _is_ios:
        await ios_connections.register(user_id, _conn_id)

    _ws_lock = asyncio.Lock()  # serialise concurrent sends on the socket
    _stop = asyncio.Event()
    # Set once the client sends any frame. Older iOS builds never do, so the
    # server keeps a periodic keepalive to them: a send error is the only way
    # to notice their socket went half-open.
    _client_heard = asyncio.Event()

    async def _send(payload: dict) -> None:
        async with _ws_lock:
            await websocket.send_json(payload)

    async def _receiver() -> None:
        """Read client frames for the life of the connection. Returning means
        the connection is over (disconnect, error, or idle timeout)."""
        heard = False
        while not _stop.is_set():
            timeout = IOS_IDLE_TIMEOUT_S if (_is_ios and heard) else None
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.info("monitor WS idle timeout: user=%s conn=%s", user_id, _conn_id)
                try:
                    await websocket.close(code=1001)
                except Exception:
                    pass
                return
            except _DISCONNECT_ERRORS:
                return
            if msg.get("type") != "websocket.receive":
                return  # websocket.disconnect
            heard = True
            _client_heard.set()
            if _is_ios:
                ios_connections.touch(user_id, _conn_id)
            text = msg.get("text")
            if not text:
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "ping":
                try:
                    await _send({"type": "pong"})
                except _DISCONNECT_ERRORS:
                    return

    async def _event_pusher(sub_queue: asyncio.Queue) -> None:
        """Drain this connection's queue and push immediately. Returns only
        when the socket is unusable."""
        while not _stop.is_set():
            try:
                event = await asyncio.wait_for(sub_queue.get(), timeout=0.3)
                batch = [event]
                while len(batch) < 50:
                    try:
                        batch.append(sub_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                async with _ws_lock:
                    for ev in batch:
                        # Only serialization failures are skipped — send
                        # errors must propagate so the disconnect path fires.
                        try:
                            safe_ev = json.loads(json.dumps(ev, default=str))
                        except Exception:
                            logger.warning("Dropping unserialisable event: %r", ev)
                            continue
                        await websocket.send_json(safe_ev)
            except asyncio.TimeoutError:
                continue
            except _DISCONNECT_ERRORS:
                return
            except Exception:
                logger.error("Unexpected error in event pusher", exc_info=True)
                return

    async def _session_watch(queue_id: object) -> None:
        """Web: periodic status_update frames. iOS: ignores status_update, so
        only watch for the agent going away (stop/restart). Returns when the
        agent entry is gone/replaced (iOS) or the socket is unusable."""
        interval = AGENT_WAIT_POLL_S if _is_ios else STATUS_INTERVAL_S
        last_keepalive = time.monotonic()
        while not _stop.is_set():
            await asyncio.sleep(interval)
            current_info = active_agents.get(user_id)
            if _is_ios:
                if not current_info or current_info.get("event_queue") is not queue_id:
                    return
                if (not _client_heard.is_set()
                        and time.monotonic() - last_keepalive >= LEGACY_KEEPALIVE_S):
                    last_keepalive = time.monotonic()
                    try:
                        await _send({"type": "keepalive"})
                    except _DISCONNECT_ERRORS:
                        return
                continue
            try:
                # During startup, agent may not be ready yet — skip status push
                agent = current_info and current_info.get("agent")
                if not agent or current_info.get("_starting"):
                    continue
                status = await asyncio.to_thread(agent.get_status)
                payload = {
                    "type": "status_update",
                    "status": status,
                    "data_store_stats": status.get("data_store_stats", {}),
                }
                await _send(json.loads(json.dumps(payload, default=str)))
            except _DISCONNECT_ERRORS:
                return
            except Exception:
                logger.error("Unexpected error in status pusher", exc_info=True)
                return

    from .agent_state import active_agents
    from .event_hub import ensure_hub

    recv_task = asyncio.create_task(_receiver())
    try:
        waiting_sent = False
        while not recv_task.done():
            agent_info = active_agents.get(user_id)
            if not agent_info:
                if not _is_ios:
                    await _send({
                        "type": "error",
                        "error": f"No active agent for {user_id}. Start it first with POST /api/agent/start",
                    })
                    break
                if not waiting_sent:
                    await _send({"type": "agent_waiting"})
                    waiting_sent = True
                await asyncio.wait({recv_task}, timeout=AGENT_WAIT_POLL_S)
                continue
            waiting_sent = False

            # Per-connection private queue from the fan-out hub. iOS loads past
            # turns via /chat-history so it gets no replay backlog; the web
            # dashboard has no history endpoint and keeps the replay.
            hub = ensure_hub(user_id, agent_info)
            sub_queue = hub.subscribe(replay=not _is_ios)
            logger.info(
                "monitor WS connect: user=%s ios=%s subscribers=%d",
                user_id, _is_ios, hub.subscriber_count,
            )
            session: list[asyncio.Task] = []
            socket_dead = False
            try:
                await _send({
                    "type": "monitor_connected",
                    "user_id": user_id,
                    "message": "Connected. Streaming agent activity...",
                })
                pusher_task = asyncio.create_task(_event_pusher(sub_queue))
                watch_task = asyncio.create_task(
                    _session_watch(agent_info.get("event_queue"))
                )
                session = [pusher_task, watch_task]
                await asyncio.wait(
                    [recv_task, *session], return_when=asyncio.FIRST_COMPLETED
                )
                # Judge before cancelling siblings (cancel() also marks done).
                socket_dead = (
                    recv_task.done()
                    or pusher_task.done()
                    or (not _is_ios and watch_task.done())  # web: watch ends on send error
                )
            finally:
                for t in session:
                    t.cancel()
                if session:
                    await asyncio.gather(*session, return_exceptions=True)
                hub.unsubscribe(sub_queue)
            if socket_dead:
                break  # otherwise the agent went away -> back to waiting
    except WebSocketDisconnect:
        logger.info(f"Monitor disconnected for {user_id} (agent continues running)")
    except _DISCONNECT_ERRORS:
        logger.info(f"Monitor socket closed for {user_id} (agent continues running)")
    except Exception as e:
        logger.error(f"Monitor error: {e}", exc_info=True)
    finally:
        _stop.set()
        recv_task.cancel()
        await asyncio.gather(recv_task, return_exceptions=True)
        agent_monitor_manager.disconnect(websocket)
        if _is_ios:
            try:
                await ios_connections.unregister(user_id, _conn_id)
            except Exception:
                pass


@router.get("/connections")
async def get_active_connections():
    """Get number of active WebSocket connections."""
    return {
        'success': True,
        'data_stream_active_connections': data_stream_manager.count(),
        'agent_monitor_active_connections': agent_monitor_manager.count()
    }
