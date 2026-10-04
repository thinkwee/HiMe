/**
 * App shell — global error / stream error banners and per-route `active` props.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../lib/api', () => import('../test/mocks/api'))

const activeProps = {}
vi.mock('../pages/Dashboard', () => ({
  default: (p) => { activeProps.dashboard = p.active; return <div>Dashboard Page</div> },
}))
vi.mock('../pages/AutonomousAgentMonitor', () => ({
  default: (p) => { activeProps.agent = p.active; return <div>Agent Page</div> },
}))
vi.mock('../pages/ReportsView', () => ({
  default: (p) => { activeProps.reports = p.active; return <div>Reports Page</div> },
}))
vi.mock('../pages/PromptEditor', () => ({ default: () => <div>Prompts Page</div> }))
vi.mock('../pages/Skills', () => ({ default: () => <div>Skills Page</div> }))
vi.mock('../pages/KnowledgeBase', () => ({
  default: (p) => { activeProps.knowledge = p.active; return <div>Knowledge Page</div> },
}))
vi.mock('../pages/PersonalisedPages', () => ({ default: () => <div>Personalised Pages Page</div> }))

import App from '../App'
import { api } from '../test/mocks/api'

describe('App banners', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.pushState({}, '', '/')
    api.getStreamConfig.mockResolvedValue({ success: true, live_history_window: '1hour' })
    api.getFeatureTypes.mockResolvedValue({ success: true, feature_types: [] })
    api.getAgentStatus.mockResolvedValue({ success: true, agents: {} })
    api.setStreamConfig.mockResolvedValue({ success: true })
    api.connectDataStream.mockImplementation(() => ({ close: vi.fn(), send: vi.fn() }))
  })

  it('renders the global error with Retry (previously never shown)', async () => {
    const user = userEvent.setup()
    api.getStreamConfig.mockResolvedValue({ success: false, error: 'HTTP 503' })
    render(<App />)

    const banner = await screen.findByRole('alert')
    expect(banner).toHaveTextContent("Couldn't load data from the server: HTTP 503")

    // Retry re-runs the bootstrap; once the backend is back the banner clears.
    api.getStreamConfig.mockResolvedValue({ success: true, live_history_window: '1hour' })
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('the banner can be dismissed', async () => {
    const user = userEvent.setup()
    api.getFeatureTypes.mockResolvedValue({ success: false, error: 'boom' })
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('tells each page whether its route is active', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('Dashboard Page')
    expect(activeProps.dashboard).toBe(true)
    expect(activeProps.reports).toBe(false)

    await user.click(screen.getByRole('link', { name: /Reports/ }))
    await waitFor(() => expect(activeProps.reports).toBe(true))
    expect(activeProps.dashboard).toBe(false)
  })
})
