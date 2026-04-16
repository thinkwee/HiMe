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
 * `useApp()` — they never manage this state locally.
 */
import { createContext, useCallback, useContext, useEffect, useReducer, useRef } from 'react'
import { api } from '../lib/api'

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
  },
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
    case 'APPEND_HISTORICAL':
      return {
        ...state,
        streaming: {
          ...state.streaming,
          historicalData: [...state.streaming.historicalData, ...action.payload],
        },
      }
    default:
      return state
  }
}

// -----------------------------------------------------------------------
// Context
// -----------------------------------------------------------------------

const AppContext = createContext(null)

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const wsRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const lastWindowRef = useRef('1hour')
  /** Whether the stream was intentionally stopped (no auto-reconnect). */
  const stoppedRef = useRef(false)
  const startStreamRef = useRef(null)

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
    try {
      stoppedRef.current = false
      lastWindowRef.current = liveHistoryWindow

      // Cancel any pending reconnect
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }

      // Close any existing WebSocket before starting a new one
      if (wsRef.current) {
        try { wsRef.current.close() } catch (_) {}
        wsRef.current = null
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

      const websocket = api.connectDataStream()

      websocket.onopen = () => {
        reconnectAttemptRef.current = 0 // reset backoff on success
        dispatch({ type: 'SET_STREAMING', payload: { isStreaming: true } })
      }

      websocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'data_batch' && data.batch) {
            dispatch({ type: 'SET_STREAMING', payload: { streamData: data.batch } })
            if (Array.isArray(data.batch.data)) {
              dispatch({ type: 'APPEND_HISTORICAL', payload: data.batch.data })
            }
          } else if (data.type === 'stream_complete' || data.type === 'error') {
            websocket.close()
            dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
            Promise.resolve(api.setStreamConfig(false)).catch(err => console.error('stream config reset failed:', err))
            if (data.type === 'error') console.error(`Stream error: ${data.error}`)
          }
        } catch (_) {}
      }

      websocket.onerror = () => {
        dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
      }

      websocket.onclose = () => {
        wsRef.current = null
        dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
        // Auto-reconnect unless intentionally stopped
        _scheduleReconnect()
      }

      wsRef.current = websocket
    } catch (err) {
      dispatch({ type: 'SET_STREAMING', payload: { isStreaming: false } })
      console.error('Failed to start stream:', err?.message || 'Unknown error')
      _scheduleReconnect()
    }
  }, [_scheduleReconnect])

  // Keep ref in sync so _scheduleReconnect can call the latest startStream
  startStreamRef.current = startStream

  /** Stop Dashboard data stream. */
  const stopStream = useCallback(() => {
    stoppedRef.current = true
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (wsRef.current) {
      try {
        wsRef.current.close()
      } catch (_) {}
      wsRef.current = null
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
      dispatch({ type: 'SET_AGENT_STATUS', payload: { running: false } })
    }
  }, [])

  // ------------------------------------------------------------------ //
  // Bootstrap
  // ------------------------------------------------------------------ //

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      dispatch({ type: 'SET_LOADING', payload: true })
      try {
        // Load saved stream config to get the preferred window
        const streamCfg = await api.getStreamConfig()
        const savedWindow = streamCfg?.live_history_window || '1hour'

        await Promise.all([
          _loadFeatureTypes(dispatch),
          refreshAgentStatus().catch(() => {}),
        ])

        // Always auto-start the stream on page load
        if (!cancelled) {
          console.log(`Auto-starting stream with window: ${savedWindow}`)
          startStream(savedWindow)
        }
      } catch (err) {
        if (!cancelled) dispatch({ type: 'SET_GLOBAL_ERROR', payload: String(err) })
      } finally {
        if (!cancelled) dispatch({ type: 'SET_LOADING', payload: false })
      }
    })()
    return () => {
      cancelled = true
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const value = {
    ...state,
    refreshAgentStatus,
    startStream,
    stopStream,
    reconnectStream,
    updateStreamingConfig,
    dispatch,
  }

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

// -----------------------------------------------------------------------
// Hook
// -----------------------------------------------------------------------

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>')
  return ctx
}

// -----------------------------------------------------------------------
// Private helpers
// -----------------------------------------------------------------------

async function _loadFeatureTypes(dispatch) {
  try {
    const res = await api.getFeatureTypes()
    if (res?.success && Array.isArray(res.feature_types)) {
      dispatch({ type: 'SET_FEATURE_TYPES', payload: res.feature_types })
    }
  } catch (_) { /* non-fatal */ }
}
