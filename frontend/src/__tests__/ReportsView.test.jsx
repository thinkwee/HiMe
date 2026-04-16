/**
 * Tests for ReportsView.jsx — the analysis reports archive.
 *
 * Covers:
 *  - Reports load and display on mount
 *  - Empty state when no reports
 *  - Error handling on API failure
 *  - Filter buttons render and function
 *  - Search input filters reports
 *  - Sort controls work
 *  - Report click opens modal
 *  - Refresh button reloads reports
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock api
vi.mock('../lib/api', () => import('../test/mocks/api'))

// Mock react-markdown to avoid ESM issues in test environment
vi.mock('react-markdown', () => ({
  default: ({ children }) => <div data-testid="markdown">{children}</div>,
}))
vi.mock('remark-gfm', () => ({
  default: () => {},
}))

import { api, mockReports } from '../test/mocks/api'
import { AppProvider } from '../context/AppContext'
import { BrowserRouter } from 'react-router-dom'
import ReportsView from '../pages/ReportsView'

function renderReports() {
  return render(
    <BrowserRouter>
      <AppProvider>
        <ReportsView />
      </AppProvider>
    </BrowserRouter>
  )
}

describe('ReportsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // queryAgentMemory is called by:
    //  1. AppContext bootstrap with 'stats'
    //  2. ReportsView with 'reports'
    // We need to handle both. Use mockImplementation to route by argument.
    api.queryAgentMemory.mockImplementation((queryType) => {
      if (queryType === 'reports') {
        return Promise.resolve({ success: true, data: mockReports })
      }
      // 'stats' for the context bootstrap
      return Promise.resolve({ success: true, data: { table_counts: {}, date_range: {} } })
    })

    // Context bootstrap mocks
    api.getStreamConfig.mockResolvedValue({ success: true, is_streaming: false, live_history_window: '1hour' })
    api.getFeatureTypes.mockResolvedValue({ success: true, feature_types: [] })
    api.getAgentStatus.mockResolvedValue({ success: true, agents: {} })
  })

  // ------------------------------------------------------------------ //
  // Reports load and display
  // ------------------------------------------------------------------ //

  it('loads and displays reports on mount', async () => {
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    expect(screen.getByText('Daily Activity Goal Achieved')).toBeInTheDocument()
    expect(screen.getByText(/Irregular Heart Rhythm Detected/)).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Page heading
  // ------------------------------------------------------------------ //

  it('renders the page heading', async () => {
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Analysis Reports')).toBeInTheDocument()
    })

    expect(screen.getByText(/archive of agent findings/i)).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Empty state
  // ------------------------------------------------------------------ //

  it('shows empty state when there are no reports', async () => {
    api.queryAgentMemory.mockImplementation((queryType) => {
      if (queryType === 'reports') {
        return Promise.resolve({ success: true, data: [] })
      }
      return Promise.resolve({ success: true, data: { table_counts: {}, date_range: {} } })
    })

    renderReports()

    await waitFor(() => {
      expect(screen.getByText('No reports found')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByText(/adjusting your filters/i)).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Error handling — API failure
  // ------------------------------------------------------------------ //

  it('shows empty state on API error', async () => {
    api.queryAgentMemory.mockImplementation((queryType) => {
      if (queryType === 'reports') {
        return Promise.reject(new Error('Network error'))
      }
      return Promise.resolve({ success: true, data: { table_counts: {}, date_range: {} } })
    })

    renderReports()

    await waitFor(() => {
      expect(screen.getByText('No reports found')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Error handling — success: false
  // ------------------------------------------------------------------ //

  it('shows empty state when API returns success: false', async () => {
    api.queryAgentMemory.mockImplementation((queryType) => {
      if (queryType === 'reports') {
        return Promise.resolve({ success: false, error: 'Agent not running' })
      }
      return Promise.resolve({ success: true, data: { table_counts: {}, date_range: {} } })
    })

    renderReports()

    await waitFor(() => {
      expect(screen.getByText('No reports found')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Filter buttons
  // ------------------------------------------------------------------ //

  it('renders filter buttons for all alert levels', async () => {
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    // Filter buttons in the filter bar
    const filterSection = screen.getByText('Filter:').parentElement
    expect(filterSection).toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Critical' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Warning' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Info' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Normal' })).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Filtering by alert level
  // ------------------------------------------------------------------ //

  it('filters reports by alert level when filter button is clicked', async () => {
    const user = userEvent.setup()
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    // Click "Critical" filter button
    await user.click(screen.getByRole('button', { name: 'Critical' }))

    await waitFor(() => {
      expect(screen.getByText(/Irregular Heart Rhythm Detected/)).toBeInTheDocument()
    })

    // The warning and normal reports should be hidden
    expect(screen.queryByText('Elevated Resting Heart Rate Detected')).not.toBeInTheDocument()
    expect(screen.queryByText('Daily Activity Goal Achieved')).not.toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Search input
  // ------------------------------------------------------------------ //

  it('filters reports by search query', async () => {
    const user = userEvent.setup()
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    const searchInput = screen.getByPlaceholderText('Search reports...')
    await user.type(searchInput, 'steps')

    await waitFor(() => {
      // Only the "Daily Activity Goal Achieved" report mentions steps in content
      expect(screen.getByText('Daily Activity Goal Achieved')).toBeInTheDocument()
    })

    expect(screen.queryByText('Elevated Resting Heart Rate Detected')).not.toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Sort controls
  // ------------------------------------------------------------------ //

  it('renders sort dropdown and toggle button', async () => {
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Sort by:')).toBeInTheDocument()
    })

    // The select should have Data Time and Generated Time
    const select = screen.getByDisplayValue('Data Time')
    expect(select).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Report click opens modal
  // ------------------------------------------------------------------ //

  it('opens a report modal when a report card is clicked', async () => {
    const user = userEvent.setup()
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    // Click the report card
    await user.click(screen.getByText('Elevated Resting Heart Rate Detected'))

    // Modal should show with the Close Report button
    await waitFor(() => {
      expect(screen.getByText('Close Report')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Modal close
  // ------------------------------------------------------------------ //

  it('closes modal when Close Report button is clicked', async () => {
    const user = userEvent.setup()
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Elevated Resting Heart Rate Detected'))

    await waitFor(() => {
      expect(screen.getByText('Close Report')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Close Report'))

    await waitFor(() => {
      expect(screen.queryByText('Close Report')).not.toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Refresh button
  // ------------------------------------------------------------------ //

  it('refresh button reloads reports', async () => {
    const user = userEvent.setup()
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Refresh')).toBeInTheDocument()
    })

    // queryAgentMemory should have been called with 'reports' at least once
    const reportsCalls = api.queryAgentMemory.mock.calls.filter(c => c[0] === 'reports')
    expect(reportsCalls.length).toBeGreaterThanOrEqual(1)

    const countBefore = api.queryAgentMemory.mock.calls.filter(c => c[0] === 'reports').length

    await user.click(screen.getByText('Refresh'))

    // Should be called again with 'reports'
    await waitFor(() => {
      const countAfter = api.queryAgentMemory.mock.calls.filter(c => c[0] === 'reports').length
      expect(countAfter).toBeGreaterThan(countBefore)
    })
  })

  // ------------------------------------------------------------------ //
  // Alert level badges are displayed on cards
  // ------------------------------------------------------------------ //

  it('displays alert level badges on report cards', async () => {
    renderReports()

    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })

    // There should be "warning", "normal", and "critical" badges
    const warningBadges = screen.getAllByText('warning')
    expect(warningBadges.length).toBeGreaterThanOrEqual(1)

    const normalBadges = screen.getAllByText('normal')
    expect(normalBadges.length).toBeGreaterThanOrEqual(1)

    const criticalBadges = screen.getAllByText('critical')
    expect(criticalBadges.length).toBeGreaterThanOrEqual(1)
  })

  // ------------------------------------------------------------------ //
  // Search clear restores all reports
  // ------------------------------------------------------------------ //

  it('clears search when clear button is clicked', async () => {
    const user = userEvent.setup()
    renderReports()

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search reports...')).toBeInTheDocument()
    })

    const searchInput = screen.getByPlaceholderText('Search reports...')
    await user.type(searchInput, 'steps')

    await waitFor(() => {
      expect(screen.queryByText('Elevated Resting Heart Rate Detected')).not.toBeInTheDocument()
    })

    // Find and click the clear button — the X button near the search input
    const clearButtons = searchInput.parentElement.querySelectorAll('button')
    expect(clearButtons.length).toBeGreaterThan(0)
    await user.click(clearButtons[0])

    // All reports should be visible again
    await waitFor(() => {
      expect(screen.getByText('Elevated Resting Heart Rate Detected')).toBeInTheDocument()
    })
  })
})
