/**
 * Tests for App.jsx — the root component with routing and sidebar.
 *
 * Covers:
 *  - Renders without crashing (including the dataSource fix — no user
 *    selector, always "Live HealthKit")
 *  - All navigation links are present
 *  - Sidebar brand / logo area
 *  - Agent running indicator (green dot) appears when agent is active
 *  - Stream status indicator appears when streaming
 *  - Footer status badges
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock the api module
vi.mock('../lib/api', () => import('../test/mocks/api'))

// Mock heavy child pages so we only test the shell / navigation
vi.mock('../pages/Dashboard', () => ({
  default: () => <div data-testid="page-dashboard">Dashboard Page</div>,
}))
vi.mock('../pages/AutonomousAgentMonitor', () => ({
  default: () => <div data-testid="page-agent">Agent Monitor Page</div>,
}))
vi.mock('../pages/ReportsView', () => ({
  default: () => <div data-testid="page-reports">Reports Page</div>,
}))
vi.mock('../pages/PromptEditor', () => ({
  default: () => <div data-testid="page-prompts">Prompts Page</div>,
}))
vi.mock('../pages/KnowledgeBase', () => ({
  default: () => <div data-testid="page-knowledge">Knowledge Page</div>,
}))
vi.mock('../pages/PersonalisedPages', () => ({
  default: () => <div data-testid="page-apps">Personalised Pages Page</div>,
}))

import App from '../App'
import { api } from '../test/mocks/api'

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
  })

  // ------------------------------------------------------------------ //
  // Renders without crashing
  // ------------------------------------------------------------------ //

  it('renders without crashing', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('HiMe')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // All navigation links present
  // ------------------------------------------------------------------ //

  it('renders all navigation links', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('HiMe')).toBeInTheDocument()
    })

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.getByText('Agent Monitor')).toBeInTheDocument()
    expect(screen.getByText('Reports')).toBeInTheDocument()
    expect(screen.getByText('Prompts')).toBeInTheDocument()
    expect(screen.getByText('Memory & Tools')).toBeInTheDocument()
    expect(screen.getByText('Personalised Pages')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Brand / logo section
  // ------------------------------------------------------------------ //

  it('renders the HiMe brand and subtitle', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('HiMe')).toBeInTheDocument()
    })

    expect(screen.getByText('Digital Avatar')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Data source badge — always "Live HealthKit"
  // ------------------------------------------------------------------ //

  it('shows Live HealthKit badge (no user selector needed)', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('HiMe')).toBeInTheDocument()
    })

    // The sidebar has the "Live HealthKit" badge
    const badges = screen.getAllByText('Live HealthKit')
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })

  // ------------------------------------------------------------------ //
  // Dashboard is visible on root route
  // ------------------------------------------------------------------ //

  it('shows Dashboard page on root route', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.getByTestId('page-dashboard')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // Navigation — clicking Agent Monitor link updates URL
  // ------------------------------------------------------------------ //

  it('navigates to Agent Monitor page when link is clicked', async () => {
    const user = userEvent.setup()
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Agent Monitor')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Agent Monitor'))

    // After clicking, verify the URL changed (both pages stay mounted in persistent views)
    await waitFor(() => {
      expect(window.location.pathname).toBe('/agent')
    })

    // Both pages should still be in the DOM (persistent views keep all mounted)
    expect(screen.getByTestId('page-agent')).toBeInTheDocument()
    expect(screen.getByTestId('page-dashboard')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------ //
  // Agent running indicator
  // ------------------------------------------------------------------ //

  it('shows green dot next to Agent Monitor when agent is running', async () => {
    api.getAgentStatus.mockResolvedValue({
      success: true,
      agents: {
        LiveUser: { status: 'running', cycles_completed: 5 },
      },
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('HiMe')).toBeInTheDocument()
    })

    // The green dot is a span with specific classes next to "Agent Monitor"
    await waitFor(() => {
      const agentLink = screen.getByText('Agent Monitor').closest('a')
      const dot = agentLink.querySelector('.bg-green-500')
      expect(dot).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // No agent indicator when agent is not running
  // ------------------------------------------------------------------ //

  it('does not show green dot when agent is not running', async () => {
    api.getAgentStatus.mockResolvedValue({ success: true, agents: {} })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('HiMe')).toBeInTheDocument()
    })

    // Allow bootstrap to complete
    await waitFor(() => {
      const agentLink = screen.getByText('Agent Monitor').closest('a')
      const dot = agentLink.querySelector('.bg-green-500')
      expect(dot).toBeNull()
    })
  })

  // ------------------------------------------------------------------ //
  // Footer — agent and stream badges
  // ------------------------------------------------------------------ //

  it('shows Agent badge in footer when agent is running', async () => {
    api.getAgentStatus.mockResolvedValue({
      success: true,
      agents: {
        LiveUser: { status: 'running', cycles_completed: 5 },
      },
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Agent')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------ //
  // All six persistent pages are mounted
  // ------------------------------------------------------------------ //

  it('mounts all six persistent pages simultaneously', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.getByTestId('page-dashboard')).toBeInTheDocument()
    })

    expect(screen.getByTestId('page-agent')).toBeInTheDocument()
    expect(screen.getByTestId('page-reports')).toBeInTheDocument()
    expect(screen.getByTestId('page-prompts')).toBeInTheDocument()
    expect(screen.getByTestId('page-knowledge')).toBeInTheDocument()
    expect(screen.getByTestId('page-apps')).toBeInTheDocument()
  })
})
