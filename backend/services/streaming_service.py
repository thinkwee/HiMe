"""
Service for handling live data streaming to WebSockets.
Streams ALL features from the health database — no feature selection.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from fastapi import WebSocket

try:
    from websockets.exceptions import ConnectionClosed as _WsConnectionClosed
    from websockets.exceptions import ConnectionClosedOK as _WsConnectionClosedOK
except ImportError:
    _WsConnectionClosed = None
    _WsConnectionClosedOK = None

try:
    from starlette.websockets import WebSocketDisconnect as _WsDisconnect
except ImportError:
    _WsDisconnect = None

from ..config import settings
from ..data_readers import create_reader
from ..utils import ts_now

logger = logging.getLogger(__name__)

# Global executor for IO-bound tasks
_executor = ThreadPoolExecutor(max_workers=10)

def shutdown_executor():
    """Shutdown the global executor."""
    logger.info("Shutting down global streaming executor...")
    _executor.shutdown(wait=False, cancel_futures=True)

def parse_window_to_minutes(window_str: str) -> int:
    """Convert window string to minutes."""
    w = (window_str or '1hour').lower()
    if w == '1hour':
        return 60
    if w == '1day':
        return 24 * 60
    if w == '1week':
        return 7 * 24 * 60
    if w == '1month':
        return 30 * 24 * 60
    return 60

class DataStreamingService:
    """Service to handle live data streaming to WebSockets."""

    @staticmethod
    async def stream_data(
        websocket: WebSocket,
        pids: list[str],
        stream_config: dict,
        active_connections: set
    ):
        """
        Stream live data to the provided websocket.

        Streams ALL features currently in the database. New feature types
        (e.g. workouts recorded mid-stream) are auto-discovered every ~30s.
        """
        try:
            loop = asyncio.get_running_loop()

            # Move reader creation + feature discovery off the event loop
            # (SQLite on CephFS can block for seconds on cold open)
            data_path = settings.DATA_STORE_PATH

            def _init_reader():
                r = create_reader("live", data_path)
                return r, r.get_feature_types()

            reader, all_features = await loop.run_in_executor(_executor, _init_reader)
            logger.info(f"Stream start: {len(all_features)} feature types, mode=live")

            # Send start event immediately so the client knows we're alive
            await websocket.send_json({
                'type': 'stream_start',
                'users': pids,
                'data_source': 'live',
                'features': all_features,
                'granularity': 'real-time'
            })

            from ..api.config_routes import get_app_state
            app_state = get_app_state()

            logger.info("Starting LIVE data stream")
            last_ts_map: dict[str, float] = {}

            # Get dynamic window
            window_str = app_state.get('live_history_window', '1hour')
            window_minutes = parse_window_to_minutes(window_str)
            logger.info(f"Live mode history window: {window_str} ({window_minutes} min)")

            # Load and send recent history
            def load_live_history():
                if hasattr(reader, 'load_features_batch'):
                    return reader.load_features_batch(
                        pids, all_features, minutes=window_minutes
                    )
                all_dfs = []
                for ft in all_features:
                    try:
                        df = reader.load_feature_data(pids, ft, minutes=window_minutes)
                        if not df.empty:
                            all_dfs.append(df)
                    except Exception as e:
                        logger.warning(f"Live history load {ft}: {e}")
                if not all_dfs:
                    return pd.DataFrame()
                return pd.concat(all_dfs, ignore_index=True).sort_values('date').reset_index(drop=True)

            hist_df = await loop.run_in_executor(_executor, load_live_history)
            if not hist_df.empty and websocket in active_connections:
                records = hist_df.to_dict('records')
                for r in records:
                    ts = r.get('ts')
                    if ts is None:
                        ts = pd.Timestamp(r['date']).timestamp()
                    ft = r.get('feature_type', '')
                    if ts > last_ts_map.get(ft, 0.0):
                        last_ts_map[ft] = ts
                def _to_iso(d):
                    if hasattr(d, 'strftime'):
                        return d.strftime('%Y-%m-%dT%H:%M:%SZ')
                    s = str(d)
                    if s and not s.endswith('Z') and '+' not in s:
                        return s + 'Z'
                    return s
                batch_data = [{'date': _to_iso(r['date']), 'value': float(r['value']),
                              'feature_type': r['feature_type'], 'pid': r.get('pid', 'LiveUser')}
                             for r in records]
                await websocket.send_json({
                    'type': 'data_batch',
                    'batch': {
                        'data': batch_data,
                        'count': len(batch_data),
                        'is_live': True,
                        'data_timestamp': ts_now(),
                        'window_start': _to_iso(hist_df['date'].min()),
                        'window_end': _to_iso(hist_df['date'].max())
                    }
                })
                logger.info(f"Live: sent {len(batch_data)} historical samples")

            _poll_count = 0
            while websocket in active_connections:
                try:
                    # Refresh feature list every ~30 polls (~30s) to pick up
                    # newly ingested feature types (e.g. first workout recorded).
                    _poll_count += 1
                    if _poll_count % 30 == 0:
                        try:
                            fresh = reader.get_feature_types()
                            if fresh:
                                for ft in fresh:
                                    if ft not in all_features:
                                        all_features.append(ft)
                                        logger.info(f"Live: discovered new feature: {ft}")
                        except Exception:
                            pass

                    # Load new samples
                    def load_new_samples():
                        if not all_features:
                            return []
                        min_ts = min(last_ts_map.get(ft, 0.0) for ft in all_features)
                        if hasattr(reader, 'load_features_batch'):
                            df = reader.load_features_batch(
                                pids, all_features, since_ts=min_ts
                            )
                            if not df.empty:
                                _ts_map = last_ts_map
                                df = df[df.apply(
                                    lambda r, m=_ts_map: r['ts'] > m.get(r['feature_type'], 0.0),  # noqa: B023
                                    axis=1
                                )]
                                for ft in df['feature_type'].unique():
                                    ft_df = df[df['feature_type'] == ft]
                                    if not ft_df.empty:
                                        last_ts_map[ft] = float(ft_df['ts'].max())
                        else:
                            all_dfs = []
                            for ft in all_features:
                                try:
                                    d = reader.load_feature_data(
                                        pids, ft, since_ts=last_ts_map.get(ft, 0.0)
                                    )
                                    if not d.empty:
                                        all_dfs.append(d)
                                        last_ts_map[ft] = float(d['ts'].max())
                                except Exception as e:
                                    logger.warning(f"Live load {ft}: {e}")
                            df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
                        if df.empty:
                            return []
                        return df.sort_values('date').reset_index(drop=True).to_dict('records')

                    records = await loop.run_in_executor(_executor, load_new_samples)
                    def _to_iso(d):
                        if hasattr(d, 'strftime'):
                            return d.strftime('%Y-%m-%dT%H:%M:%SZ')
                        s = str(d)
                        if s and not s.endswith('Z') and '+' not in s:
                            return s + 'Z'
                        return s
                    batch = [{'date': _to_iso(r['date']), 'value': float(r['value']),
                             'feature_type': r['feature_type'], 'pid': r.get('pid', 'LiveUser')}
                            for r in records]

                    if batch:
                        if websocket not in active_connections:
                            break
                        await websocket.send_json({
                            'type': 'data_batch',
                            'batch': {
                                'data': batch,
                                'count': len(batch),
                                'is_live': True,
                                'data_timestamp': ts_now()
                            }
                        })

                    await asyncio.sleep(1)
                except Exception as e:
                    _disconnect_types = tuple(t for t in [
                        _WsConnectionClosed, _WsConnectionClosedOK, _WsDisconnect
                    ] if t is not None)
                    if _disconnect_types and isinstance(e, _disconnect_types):
                        break
                    err_str = str(e)
                    if "1005" in err_str or "1000" in err_str or "1001" in err_str or "connection closed" in err_str.lower():
                        break
                    logger.error(f"Error in live stream loop: {e}", exc_info=True)
                    break

        except Exception as e:
            logger.error(f"Error in streaming service: {e}", exc_info=True)
            if websocket in active_connections:
                try:
                    await websocket.send_json({'type': 'error', 'error': str(e)})
                except Exception:
                    pass
