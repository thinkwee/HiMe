/**
 * AppContext — global application state shared across all pages.
 *
 * Stores:
 *  - featureTypes: available feature type strings (all Apple Health + Workout features)
 *  - agentStatus: current running status of the autonomous agent
 *  - streaming: Dashboard stream state (persists across route changes)
 *
 * Single-user mode: always uses "LiveUser" — no user selection needed.
 *
 * All mutations go through the provided actions. Components only consume
 * `useApp()` (state + actions) or `useAppActions()` (actions only, stable) —
 * they never manage this state locally.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef } from 'react'
import { api } from '../lib/api'
import { parseBackendDate } from '../lib/utils'

// -----------------------------------------------------------------------
// Shape
// -----------------------------------------------------------------------

const initialState = {
  /** List of feature type strings (all features from DB) */
  featureTypes: [],
  /** { running: bool, user_id, status, config } | null */
  agentStatus: null,
  /** Error string for the most recent failed global operation */
  globalError: null,
  /** True while initial data is loading */
  loading: true,
  /** Dashboard stream — persists across route changes so stream keeps running */
  streaming: {
    isStreaming: false,
    streamData: null,
    historicalData: [],
    liveHistoryWindow: '1hour',
    /** Last error frame / failure on the data stream (cleared once reconnected). */
    streamError: null,
  },
}

// -----------------------------------------------------------------------
// Historical buffer limits
// -----------------------------------------------------------------------
// The stream appends live batches forever, so without a bound the buffer
// grows all day. Nothing older than the widest selectable window (1 month)
// can ever be rendered, so those records are dropped. Trimming only kicks in
// past MAX and cuts down to TARGET so the cost is amortised across batches.

const HISTORY_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
const HISTORY_MAX_RECORDS = 100000
const HISTORY_TARGET_RECORDS = 80000

function _trimHistorical(records) {
  if (records.length <= HISTORY_MAX_RECORDS) return records
  const tsOf = (r) => (r && r.date ? parseBackendDate(r.date).getTime() : NaN)
  let maxTs = null
  for (const r of records) {
    const ts = tsOf(r)
    if (!isNaN(ts) && (maxTs === null || ts > maxTs)) maxTs = ts
  }
  let trimmed = records
  if (maxTs !== null) {
    const cutoff = maxTs - HISTORY_RETENTION_MS
    trimmed = records.filter((r) => {
      const ts = tsOf(r)
      return isNaN(ts) || ts >= cutoff
    })
  }
  return trimmed.length > HISTORY_TARGET_RECORDS
    ? trimmed.slice(trimmed.length - HISTORY_TARGET_RECORDS)
    : trimmed
}

// -----------------------------------------------------------------------
// Reducer
// -----------------------------------------------------------------------

function reducer(state, action) {
  switch (action.type) {
    case 'SET_FEATURE_TYPES':
      return { ...state, featureTypes: action.payload }
    case 'SET_AGENT_STATUS':
      return { ...state, agentStatus: action.payload }
    case 'SET_GLOBAL_ERROR':
      return { ...state, globalError: action.payload }
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    case 'SET_STREAMING':
      return { ...state, streaming: { ...state.streaming, ...action.payload } }
    case 'STREAM_BATCH': {
      // One state transition per batch (latest batch + appended history), so
      // consumers re-render once instead of twice.
      const { batch, records } = action.payload
      const prev = state.streaming.historicalData
      return {
        ...state,
        streaming: {
          ...state.streaming,
          streamData: batch,
          historicalData: records.length ? _trimHistorical(prev.concat(records)) : prev,
        },
      }
    }
    case 'APPEND_HISTORICAL':
      return {
        ...state,
        streaming: {
          ...state.streaming,
          historicalData: _trimHistorical(state.streaming.historicalData.concat(action.payload)),
        },
      }
    default:
      return state
  }
}

// -----------------------------------------------------------------------
// Context
// -----------------------------------------------------------------------

// State and actions live in separate contexts: every live batch changes the
// state, but the actions are stable, so components that only need to dispatch
// (e.g. the agent monitor) don't re-render on each batch.
const AppContext = createContext(null)
const AppActionsContext = createContext(null)

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const wsRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const lastWindowRef = useRef('1hour')
  /** Whether the stream was intentionally stopped (no auto-reconnect). */
  const stoppedRef = useRef(false)
  const startStreamRef = useRef(null)
  /** Monotonic token: a startStream() superseded while awaiting aborts. */
  const streamSeqRef = useRef(0)
  /** Set on unmount so late async work doesn't open a socket nobody owns. */
  const unmountedRef = useRef(false)

  // ------------------------------------------------------------------ //
  // Actions
  // ------------------------------------------------------------------ //

  /** Schedule a data-stream reconnect with exponential back-off. */
  const _scheduleReconnect = useCallback(() => {
    if (stoppedRef.current) return
    if (reconnectTimerRef.current) return // already scheduled
    const attempt = reconnectAttemptRef.current
    const delay = Math.min(2000 * 2 ** attempt, 30000) // 2s, 4s, 8s … 30s cap
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null
      reconnectAttemptRef.current = attempt + 1
      startStreamRef.current?.(lastWindowRef.current)
    }, delay)
  }, [])

  /** Start Dashboard data stream. Backend streams ALL features automatically.
   *  Always fetches 1month from backend; window filtering is done client-side. */
  const startStream = useCallback(async (liveHistoryWindow = '1hour') => {
    const seq = ++streamSeqRef.current
    const superseded = () => seq !== streamSeqRef.current || unmountedRef.current
    try {
      stoppedRef.current = false
      lastWindowRef.current = liveHistoryWindow

      // Cancel any pending reconnect
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }

      // Close any existing WebSocket before starting a new one. The ref is
      // cleared first so the old socket's async onclose is ignored.
      if (wsRef.current) {
        const old = wsRef.current
        wsRef.current = null
        try { old.close() } catch (err) { console.error('stream close failed:', err) }
      }

      dispatch({
        type: 'SET_STREAMING',
        payload: {
          isStreaming: false,
          streamData: null,
          historicalData: [],
          liveHistoryWindow,
        },
      })

      // Always request the widest window from backend; narrower views
      // are filtered client-side in StatisticsPanel.
      await api.setStreamConfig(true, '1month')
      // A newer startStream()/stopStream()/unmount happened while awaiting.
      if (superseded() || stoppedRef.current) return

      const websocket = api.connectDataStream()

      websocket.onopen = () => {
        if (wsRef.current !== websocket) return
        reconnectAttemptRef.current = 0 // reset backoff on success
        dispatch({ type: 'SET_STREAMING', payload: { isStreaming: true, streamError: null } })
      }

      websocket.onmessage = (event) => {
        if (wsRef.current !== websocket) return
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'data_batch' && data.batch) {
            dispatch({
              type: 'STREAM_BATCH',
              payload: {
                batch: data.batch,
                records: Array.isArray(data.batch.data) ? data.batch.data : [],
              },
            })
          } else if (data.type === 'error') {
            // A server-side error frame is not a reason to give up for good:
            // surface it and let the close handler reconnect with back-off.
            // (The server sends no other terminal frame.)
            console.error(`Stream error: ${data.error}`)
            dispatch({
              type: 'SET_STREAMING',
              payload: { isStreaming: false, streamError: String(data.error || 'Stream error') },
            })
            try { websocket.close() } catch (err) { console.error('stream close failed:', err) }
          }
        } catch (err) {
          console.error('Failed to parse stream message:', err)
        }
      }

      websocket.onerror = () => {
        if (wsRef.current !== websocket) return
        dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
      }

      websocket.onclose = () => {
        // close() fires asynchronously: a socket replaced by a newer one must
        // not clear the live ref, flip isStreaming or schedule a reconnect.
        if (wsRef.current !== websocket) return
        wsRef.current = null
        dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
        // Auto-reconnect unless intentionally stopped
        _scheduleReconnect()
      }

      wsRef.current = websocket
    } catch (err) {
      if (superseded()) return
      dispatch({
        type: 'SET_STREAMING',
        payload: { isStreaming: false, streamError: err?.message || 'Failed to start stream' },
      })
      console.error('Failed to start stream:', err?.message || 'Unknown error')
      _scheduleReconnect()
    }
  }, [_scheduleReconnect])

  // Keep ref in sync so _scheduleReconnect can call the latest startStream
  useEffect(() => {
    startStreamRef.current = startStream
  }, [startStream])

  /** Stop Dashboard data stream. */
  const stopStream = useCallback(() => {
    stoppedRef.current = true
    streamSeqRef.current += 1 // abort any startStream() still awaiting
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (wsRef.current) {
      const old = wsRef.current
      wsRef.current = null
      try {
        old.close()
      } catch (err) {
        console.error('stream close failed:', err)
      }
    }
    dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
    Promise.resolve(api.setStreamConfig(false)).catch(err => console.error('stream config reset failed:', err))
  }, [])

  /** Switch the visible time window. Pure client-side — no reconnect needed
   *  because we always fetch 1month from the backend. */
  const reconnectStream = useCallback((newWindow) => {
    dispatch({ type: 'SET_STREAMING', payload: { liveHistoryWindow: newWindow } })
    // Persist the user's preference so it's restored on next page load
    api.setStreamConfig(null, newWindow).catch(() => {})
  }, [])

  /** Update streaming config — used by Dashboard form. */
  const updateStreamingConfig = useCallback((updates) => {
    dispatch({ type: 'SET_STREAMING', payload: updates })
  }, [])

  /** Re-fetch agent status (useful for polling) */
  const refreshAgentStatus = useCallback(async () => {
    try {
      const res = await api.getAgentStatus()
      // Only a successful response may change the status: a failed poll
      // (network blip, 5xx) says nothing about whether the agent is running.
      if (res?.success) {
        const agents = res.agents || {}
        const firstPid = Object.keys(agents)[0]
        if (firstPid) {
          dispatch({ type: 'SET_AGENT_STATUS', payload: { ...agents[firstPid], running: true, user_id: firstPid } })
        } else {
          dispatch({ type: 'SET_AGENT_STATUS', payload: { running: false } })
        }
      }
    } catch (_) {
      // keep the previous state
    }
  }, [])

  // ------------------------------------------------------------------ //
  // Bootstrap
  // ------------------------------------------------------------------ //

  const bootstrap = useCallback(async (isCancelled = () => false) => {
    dispatch({ type: 'SET_LOADING', payload: true })
    dispatch({ type: 'SET_GLOBAL_ERROR', payload: null })
    try {
      // Load saved stream config to get the preferred window
      const streamCfg = await api.getStreamConfig()
      const savedWindow = streamCfg?.live_history_window || '1hour'

      const [featureErr] = await Promise.all([
        _loadFeatureTypes(dispatch),
        refreshAgentStatus().catch(() => {}),
      ])

      if (isCancelled()) return
      // Backend unreachable / rejected: say so instead of showing empty pages.
      const failure = (streamCfg && streamCfg.success === false && streamCfg.error) || featureErr
      if (failure) dispatch({ type: 'SET_GLOBAL_ERROR', payload: String(failure) })

      // Always auto-start the stream on page load
      console.log(`Auto-starting stream with window: ${savedWindow}`)
      startStream(savedWindow)
    } catch (err) {
      if (!isCancelled()) dispatch({ type: 'SET_GLOBAL_ERROR', payload: String(err) })
    } finally {
      if (!isCancelled()) dispatch({ type: 'SET_LOADING', payload: false })
    }
  }, [refreshAgentStatus, startStream])

  useEffect(() => {
    unmountedRef.current = false
    let cancelled = false
    // set-state-in-effect is a false positive: bootstrap only dispatches after
    // its first await, and dispatch is not a React setState anyway.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    bootstrap(() => cancelled)
    return () => {
      cancelled = true
      unmountedRef.current = true
      stoppedRef.current = true
      streamSeqRef.current += 1
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      // Close the data socket so it doesn't leak (or reconnect) after unmount.
      if (wsRef.current) {
        const old = wsRef.current
        wsRef.current = null
        try { old.close() } catch (err) { console.error('stream close failed:', err) }
      }
    }
  }, [bootstrap])

  /** Re-run the initial load (used by the global error banner's Retry). */
  const reload = useCallback(() => bootstrap(), [bootstrap])

  const actions = useMemo(() => ({
    refreshAgentStatus,
    startStream,
    stopStream,
    reconnectStream,
    updateStreamingConfig,
    reload,
    dispatch,
  }), [refreshAgentStatus, startStream, stopStream, reconnectStream, updateStreamingConfig, reload])

  const value = useMemo(() => ({ ...state, ...actions }), [state, actions])

  return (
    <AppActionsContext.Provider value={actions}>
      <AppContext.Provider value={value}>{children}</AppContext.Provider>
    </AppActionsContext.Provider>
  )
}

// -----------------------------------------------------------------------
// Hooks
// -----------------------------------------------------------------------

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>')
  return ctx
}

/** Stable actions only — does not re-render on state changes (e.g. each live batch). */
export function useAppActions() {
  const ctx = useContext(AppActionsContext)
  if (!ctx) throw new Error('useAppActions must be used inside <AppProvider>')
  return ctx
}

// -----------------------------------------------------------------------
// Private helpers
// -----------------------------------------------------------------------

/** Loads feature types; returns an error string on failure (non-fatal), else ''. */
async function _loadFeatureTypes(dispatch) {
  try {
    const res = await api.getFeatureTypes()
    if (res?.success && Array.isArray(res.feature_types)) {
      dispatch({ type: 'SET_FEATURE_TYPES', payload: res.feature_types })
      return ''
    }
    return (res && res.success === false && res.error) || ''
  } catch (err) {
    return err?.message || ''
  }
}
