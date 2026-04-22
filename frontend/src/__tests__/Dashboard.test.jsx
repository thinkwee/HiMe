/**
 * Tests for Dashboard.jsx — the main data streaming page.
 *
 * After the auto-stream redesign, the stream starts automatically when
 * AppContext mounts. There is no Start/Stop Stream button. The time window
 * selector lives inside StatisticsPanel and triggers a stream reconnect.
 */
import { render, screen, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock api
vi.mock('../lib/api', () => import('../test/mocks/api'))

// Mock StatisticsPanel — renders a simplified version with the key props
vi.mock('../components/StatisticsPanel', () => ({
  default: ({ data, historicalData, liveHistoryWindow, setLiveHistoryWindow, isStreaming }) => (
    <div data-testid="statistics-panel">
      <span data-testid="panel-window">{liveHistoryWindow}</span>
      <span data-testid="panel-data">{JSON.stringify(data)}</span>
      <span data-testid="panel-streaming">{String(!!isStreaming)}</span>
      {setLiveHistoryWindow && <span data-testid="panel-has-setter">yes</span>}
    </div>
  ),
}))

import { api } from '../test/mocks/api'

// We need to provide the AppContext to Dashboard
import { AppProvider } from '../context/AppContext'
import { BrowserRouter } from 'react-router-dom'
import Dashboard from '../pages/Dashboard'

function renderDashboard() {
  return render(
    <BrowserRouter>
      <AppProvider>
        <Dashboard />
      </AppProvider>
    </BrowserRouter>
  )
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })

    api.getStreamConfig.mockResolvedValue({
      success: true,
      is_streaming: false,
      live_history_window: '1hour',
    })
    api.getFeatureTypes.mockResolvedValue({
      success: true,
      feature_types: ['Heart Rate', 'Steps'],
    })
    api.getAgentStatus.mockResolvedValue({ success: true, agents: {} })
    api.getFeatureMetadata.mockResolvedValue({
      success: true,
      features: {
        'Heart Rate': { unit: 'BPM', category: 'Vitals' },
        'Steps': { unit: 'Count', category: 'Activity' },
      },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // ------------------------------------------------------------------ //
  // Renders with heading and badge
  // ------------------------------------------------------------------ //

  it('renders the Dashboard heading and data source badge', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })

    expect(screen.getByText('Live HealthKit')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // No Start/Stop Stream button (auto-start)
  // ------------------------------------------------------------------ //

  it('does not render a Start Stream or Stop Stream button', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })

    expect(screen.queryByText('Start Stream')).not.toBeInTheDocument()
    expect(screen.queryByText('Stop Stream')).not.toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // StatisticsPanel is always rendered (Data Overview section)
  // ------------------------------------------------------------------ //

  it('always renders StatisticsPanel (Data Overview) with correct props', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByTestId('statistics-panel')).toBeInTheDocument()
    })

    // liveHistoryWindow prop is passed
    expect(screen.getByTestId('panel-window')).toHaveTextContent('1hour')
    // setLiveHistoryWindow callback is provided
    expect(screen.getByTestId('panel-has-setter')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Feature metadata loads on mount
  // ------------------------------------------------------------------ //

  it('calls getFeatureMetadata on mount', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(api.getFeatureMetadata).toHaveBeenCalledTimes(1)
    })
  })

  // ------------------------------------------------------------------ //
  // Stream auto-starts on mount via AppContext bootstrap
  // ------------------------------------------------------------------ //

  it('auto-starts the stream on mount (setStreamConfig + connectDataStream called)', async () => {
    renderDashboard()

    await act(async () => {
      vi.advanceTimersByTime(100)
    })

    await waitFor(() => {
      expect(api.setStreamConfig).toHaveBeenCalledWith(true, '1month')
    })
    await waitFor(() => {
      expect(api.connectDataStream).toHaveBeenCalled()
    })
  })

  // ------------------------------------------------------------------ //
  // Real-time monitoring description
  // ------------------------------------------------------------------ //

  it('shows real-time monitoring description', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText(/real-time live health management/i)).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Streaming state is passed to StatisticsPanel
  // ------------------------------------------------------------------ //

  it('passes isStreaming to StatisticsPanel', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByTestId('panel-streaming')).toBeInTheDocument()
    })
  })
})
