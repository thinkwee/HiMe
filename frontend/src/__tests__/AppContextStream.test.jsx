/**
 * AppContext — data-stream robustness: error frames, unmount cleanup,
 * overlapping starts, single-dispatch batches.
 */
import { renderHook, waitFor, act } from '@testing-library/react'

vi.mock('../lib/api', () => import('../test/mocks/api'))

import { AppProvider, useApp } from '../context/AppContext'
import { api } from '../test/mocks/api'

function renderAppHook() {
  const wrapper = ({ children }) => <AppProvider>{children}</AppProvider>
  return renderHook(() => useApp(), { wrapper })
}

/** Controllable stand-in for the data socket (jsdom's real WebSocket would try to connect). */
function makeFakeSocket() {
  const ws = {
    onopen: null, onmessage: null, onclose: null, onerror: null,
    close: vi.fn(() => { ws.onclose?.(new Event('close')) }),
    send: vi.fn(),
  }
  return ws
}

const open = (ws) => act(() => { ws.onopen?.(new Event('open')) })

const lastSocket = () => {
  const results = api.connectDataStream.mock.results
  return results[results.length - 1].value
}

describe('AppContext data stream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getStreamConfig.mockResolvedValue({ success: true, live_history_window: '1hour' })
    api.getFeatureTypes.mockResolvedValue({ success: true, feature_types: [] })
    api.getAgentStatus.mockResolvedValue({ success: true, agents: {} })
    api.setStreamConfig.mockResolvedValue({ success: true })
    api.connectDataStream.mockImplementation(() => makeFakeSocket())
  })

  it('an error frame surfaces a banner state and reconnects instead of stopping for good', async () => {
    const { result } = renderAppHook()
    await waitFor(() => expect(api.connectDataStream).toHaveBeenCalledTimes(1))
    open(lastSocket())
    expect(result.current.streaming.isStreaming).toBe(true)

    vi.useFakeTimers()
    try {
      const ws = lastSocket()
      act(() => { ws.onmessage({ data: JSON.stringify({ type: 'error', error: 'db locked' }) }) })
      expect(result.current.streaming.streamError).toBe('db locked')
      expect(result.current.streaming.isStreaming).toBe(false)
      // the stream config is NOT switched off by an error frame
      expect(api.setStreamConfig).not.toHaveBeenCalledWith(false)

      await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
      expect(api.connectDataStream).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
    // the fresh connection clears the banner once it opens
    expect(result.current.streaming.streamError).toBe('db locked')
    open(lastSocket())
    expect(result.current.streaming.isStreaming).toBe(true)
    expect(result.current.streaming.streamError).toBeNull()
  })

  it('applies a data_batch as a single state update (latest batch + history)', async () => {
    const { result } = renderAppHook()
    await waitFor(() => expect(api.connectDataStream).toHaveBeenCalledTimes(1))
    const ws = lastSocket()
    open(ws)
    const batch = { data: [{ date: '2026-03-20T10:00:00', feature_type: 'steps', value: 1, pid: 'LiveUser' }] }
    act(() => { ws.onmessage({ data: JSON.stringify({ type: 'data_batch', batch }) }) })
    expect(result.current.streaming.streamData).toEqual(batch)
    expect(result.current.streaming.historicalData).toHaveLength(1)
  })

  it('closes the data socket on unmount and does not reconnect afterwards', async () => {
    const { unmount } = renderAppHook()
    await waitFor(() => expect(api.connectDataStream).toHaveBeenCalledTimes(1))
    const ws = lastSocket()
    open(ws)
    unmount()
    expect(ws.close).toHaveBeenCalled()
    vi.useFakeTimers()
    try {
      await vi.advanceTimersByTimeAsync(40000)
    } finally {
      vi.useRealTimers()
    }
    expect(api.connectDataStream).toHaveBeenCalledTimes(1)
  })

  it('overlapping startStream calls open one live socket (superseded call aborts)', async () => {
    const { result } = renderAppHook()
    await waitFor(() => expect(api.connectDataStream).toHaveBeenCalledTimes(1))
    open(lastSocket())
    api.connectDataStream.mockClear()

    let release
    api.setStreamConfig.mockImplementationOnce(() => new Promise((r) => { release = r }))
    let first
    act(() => { first = result.current.startStream('1day') })
    // second call supersedes the first while the first is still awaiting the config POST
    await act(async () => { await result.current.startStream('1week') })
    expect(api.connectDataStream).toHaveBeenCalledTimes(1)
    await act(async () => { release({ success: true }); await first })
    expect(api.connectDataStream).toHaveBeenCalledTimes(1)
    expect(result.current.streaming.liveHistoryWindow).toBe('1week')
  })

  it('reports an unreachable backend via globalError', async () => {
    api.getStreamConfig.mockResolvedValue({ success: false, error: 'HTTP 500' })
    const { result } = renderAppHook()
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.globalError).toBe('HTTP 500')
  })
})
