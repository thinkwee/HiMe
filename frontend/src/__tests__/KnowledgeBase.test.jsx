/**
 * Tests for KnowledgeBase.jsx (MemoryAndTools component).
 *
 * Covers:
 *  - Memory stats load and display on mount
 *  - Tools list loads and displays
 *  - Error state shown on API failure
 *  - Tab switching between Memory and Tools
 *  - Table expansion (inspect) works
 *  - Loading state displayed
 *  - Stats cards show correct aggregated data
 *  - Table type badges (System vs Agent)
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock api
vi.mock('../lib/api', () => import('../test/mocks/api'))

import {
  api,
  mockMemoryStats,
  mockTools,
  mockTableInspectData,
} from '../test/mocks/api'
import { AppProvider } from '../context/AppContext'
import { BrowserRouter } from 'react-router-dom'
import KnowledgeBase from '../pages/KnowledgeBase'

function renderKnowledgeBase() {
  return render(
    <BrowserRouter>
      <AppProvider>
        <KnowledgeBase />
      </AppProvider>
    </BrowserRouter>
  )
}

describe('KnowledgeBase (MemoryAndTools)', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // Default mocks for the component's own data fetching.
    // queryAgentMemory is called by both AppContext bootstrap ('stats')
    // and by KnowledgeBase itself ('stats'). Both return the same data.
    api.queryAgentMemory.mockResolvedValue({ success: true, data: mockMemoryStats })
    api.getTools.mockResolvedValue({ success: true, tools: mockTools })
    api.inspectMemoryTable.mockResolvedValue(mockTableInspectData)

    // Context bootstrap mocks
    api.getStreamConfig.mockResolvedValue({ success: true, is_streaming: false, live_history_window: '1hour' })
    api.getFeatureTypes.mockResolvedValue({ success: true, feature_types: [] })
    api.getAgentStatus.mockResolvedValue({ success: true, agents: {} })
  })

  // ------------------------------------------------------------------ //
  // Page heading
  // ------------------------------------------------------------------ //

  it('renders the page heading', async () => {
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('Memory & Tools')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Tab buttons
  // ------------------------------------------------------------------ //

  it('renders Memory and Tools tab buttons', async () => {
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('Memory')).toBeInTheDocument()
    })

    expect(screen.getByText('Tools')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Memory tab — stats cards
  // ------------------------------------------------------------------ //

  it('shows memory stats cards with correct values', async () => {
    renderKnowledgeBase()

    // Wait for loading to finish — table names should appear
    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    // 4 tables in our mock data
    expect(screen.getByText('4')).toBeInTheDocument()

    // Total rows = 12 + 340 + 5 + 28 = 385
    expect(screen.getByText('385')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Memory tab — table list
  // ------------------------------------------------------------------ //

  it('shows table names from memory stats', async () => {
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    expect(screen.getByText('activity_log')).toBeInTheDocument()
    expect(screen.getByText('user_notes')).toBeInTheDocument()
    expect(screen.getByText('health_insights')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Memory tab — table type badges
  // ------------------------------------------------------------------ //

  it('shows System badge for reports and activity_log, Agent for others', async () => {
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    const systemBadges = screen.getAllByText('System')
    const agentBadges = screen.getAllByText('Agent')

    // reports and activity_log are system tables
    expect(systemBadges.length).toBe(2)
    // user_notes and health_insights are agent tables
    expect(agentBadges.length).toBe(2)
  })

  // ------------------------------------------------------------------ //
  // Memory tab — inspect / expand table
  // ------------------------------------------------------------------ //

  it('expands a table when Inspect button is clicked', async () => {
    const user = userEvent.setup()
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    // Find all Inspect buttons and click the first one
    const inspectButtons = screen.getAllByText('Inspect')
    await user.click(inspectButtons[0])

    // The inspectMemoryTable should be called
    await waitFor(() => {
      expect(api.inspectMemoryTable).toHaveBeenCalled()
    })

    // Table data should be displayed
    await waitFor(() => {
      expect(screen.getByText('Test Report')).toBeInTheDocument()
    })

    expect(screen.getByText('Another Report')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Memory tab — collapse expanded table
  // ------------------------------------------------------------------ //

  it('collapses a table when Collapse button is clicked', async () => {
    const user = userEvent.setup()
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    // Expand first
    const inspectButtons = screen.getAllByText('Inspect')
    await user.click(inspectButtons[0])

    await waitFor(() => {
      expect(screen.getByText('Test Report')).toBeInTheDocument()
    })

    // Now collapse
    await user.click(screen.getByText('Collapse'))

    await waitFor(() => {
      expect(screen.queryByText('Test Report')).not.toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Switch to Tools tab
  // ------------------------------------------------------------------ //

  it('switches to Tools tab and shows tool list', async () => {
    const user = userEvent.setup()
    renderKnowledgeBase()

    // Wait for loading to finish
    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Tools'))

    await waitFor(() => {
      // Tool names from our mock
      expect(screen.getByText('sql')).toBeInTheDocument()
    })

    expect(screen.getByText('code')).toBeInTheDocument()
    expect(screen.getByText('push_report')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Tools tab — tool descriptions
  // ------------------------------------------------------------------ //

  it('shows tool descriptions on the Tools tab', async () => {
    const user = userEvent.setup()
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Tools'))

    await waitFor(() => {
      expect(screen.getByText(/SQL queries on health_data/i)).toBeInTheDocument()
    })

    expect(screen.getByText(/Execute Python code/i)).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Tools tab — parameter info
  // ------------------------------------------------------------------ //

  it('shows tool parameters on the Tools tab', async () => {
    const user = userEvent.setup()
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Tools'))

    await waitFor(() => {
      // The "query" parameter from the sql tool, with required marker
      expect(screen.getByText('query*')).toBeInTheDocument()
    })

    // The "code" parameter from the code tool
    expect(screen.getByText('code*')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Error state — API failure
  // ------------------------------------------------------------------ //

  it('shows error message when API fails', async () => {
    api.getTools.mockRejectedValue(new Error('Server unavailable'))
    api.queryAgentMemory.mockRejectedValue(new Error('Server unavailable'))

    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('Server unavailable')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Error state — success: false from tools
  // ------------------------------------------------------------------ //

  it('shows error when tools API returns success: false', async () => {
    api.getTools.mockResolvedValue({ success: false, error: 'Agent not initialized' })
    // queryAgentMemory still needs to succeed for context bootstrap
    api.queryAgentMemory.mockResolvedValue({ success: true, data: mockMemoryStats })

    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('Agent not initialized')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Error state — inspect table fails
  // ------------------------------------------------------------------ //

  it('shows error when table inspect fails', async () => {
    const user = userEvent.setup()
    api.inspectMemoryTable.mockResolvedValue({
      success: false,
      error: 'Table not found',
    })

    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    const inspectButtons = screen.getAllByText('Inspect')
    await user.click(inspectButtons[0])

    await waitFor(() => {
      expect(screen.getByText('Table not found')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Loading state
  // ------------------------------------------------------------------ //

  it('shows loading state while fetching data', async () => {
    // Make the APIs hang forever so loading state persists
    api.getTools.mockReturnValue(new Promise(() => {}))
    api.queryAgentMemory.mockReturnValue(new Promise(() => {}))

    renderKnowledgeBase()

    // The "Synchronizing..." text appears during loading
    await waitFor(() => {
      expect(screen.getByText('Synchronizing...')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Row counts shown correctly
  // ------------------------------------------------------------------ //

  it('displays row counts for each table', async () => {
    renderKnowledgeBase()

    await waitFor(() => {
      expect(screen.getByText('reports')).toBeInTheDocument()
    })

    expect(screen.getByText('12')).toBeInTheDocument()   // reports
    expect(screen.getByText('340')).toBeInTheDocument()   // activity_log
    expect(screen.getByText('5')).toBeInTheDocument()     // user_notes
    expect(screen.getByText('28')).toBeInTheDocument()    // health_insights
  })
})
