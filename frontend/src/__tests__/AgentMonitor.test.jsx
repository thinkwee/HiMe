/**
 * AutonomousAgentMonitor — rendering of agent WS events, replay de-duplication,
 * agent_waiting handling and stop/trigger error paths.
 */
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../lib/api', () => import('../test/mocks/api'))

import AutonomousAgentMonitor from '../pages/AutonomousAgentMonitor'
import { AppProvider } from '../context/AppContext'
import { api } from '../test/mocks/api'

/** Controllable stand-in for the agent monitor socket. */
function makeFakeSocket() {
  const ws = {
    onopen: null, onmessage: null, onclose: null, onerror: null,
    close: vi.fn(() => { ws.onclose?.(new Event('close')) }),
    send: vi.fn(),
    emit(obj) { ws.onmessage?.({ data: JSON.stringify(obj) }) },
  }
  return ws
}

const RUNNING = {
  success: true,
  running: true,
  config: { llm_provider: 'gemini', model: 'm' },
  status: { state: 'idle', cycle_count: 0 },
}

let socket

function renderMonitor(props = {}) {
  return render(
    <AppProvider>
      <AutonomousAgentMonitor active {...props} />
    </AppProvider>,
  )
}

async function connect() {
  await waitFor(() => expect(api.connectAgentMonitor).toHaveBeenCalled())
  act(() => { socket.onopen?.(new Event('open')) })
}

describe('AutonomousAgentMonitor', () => {
  beforeEach(() => {
    socket = makeFakeSocket()
    api.connectAgentMonitor.mockImplementation(() => socket)
    api.getAgentStatus.mockResolvedValue(RUNNING)
    api.getAgentActivity.mockResolvedValue({ success: true, events: [] })
    api.getDefaults.mockResolvedValue({ success: true })
    api.getAgentLastConfig.mockResolvedValue({ success: true, config: {} })
    api.getScheduledTasks.mockResolvedValue({ success: true, tasks: [] })
    api.getTriggerRules.mockResolvedValue({ success: true, rules: [] })
    api.getStreamConfig.mockResolvedValue({ success: true, live_history_window: '1hour' })
    api.getFeatureTypes.mockResolvedValue({ success: true, feature_types: [] })
    api.setStreamConfig.mockResolvedValue({ success: true })
    api.stopAutonomousAgent.mockResolvedValue({ success: true })
    api.triggerAnalysis.mockResolvedValue({ success: true })
    api.fetchChatImage.mockResolvedValue({ success: false, error: 'HTTP 404' })
    api.connectDataStream.mockImplementation(() => makeFakeSocket())
  })

  it('shows the assistant reply from a chat_reply event', async () => {
    renderMonitor()
    await connect()
    act(() => socket.emit({ type: 'chat_reply', content: 'You slept 7h 20m.', chat_id: 'ios:LiveUser', final: true }))
    expect(await screen.findByText(/You slept 7h 20m\./)).toBeInTheDocument()
  })

  it('shows chat_image caption and an image-load failure note when the authed fetch fails', async () => {
    renderMonitor()
    await connect()
    act(() => socket.emit({ type: 'chat_image', caption: 'HRV trend', url: '/api/agent/chat-image/abc', image_id: 'abc' }))
    expect(await screen.findByText('HRV trend')).toBeInTheDocument()
    // fetched through the authed api helper (never a ?token= URL)
    await waitFor(() => expect(api.fetchChatImage).toHaveBeenCalledWith('/api/agent/chat-image/abc'))
    expect(await screen.findByText('Image could not be loaded')).toBeInTheDocument()
  })

  it('renders plan_tool_* and chat_cleared events', async () => {
    renderMonitor()
    await connect()
    act(() => {
      socket.emit({ type: 'plan_tool_call', tool: 'sql', arguments: { query: 'SELECT plan_marker FROM t' } })
      socket.emit({ type: 'chat_cleared', chat_id: 'ios:LiveUser' })
    })
    expect(await screen.findByText(/SELECT plan_marker FROM t/)).toBeInTheDocument()
    expect(screen.getByText(/Chat history cleared/)).toBeInTheDocument()
  })

  it('ignores unknown event types without crashing', async () => {
    const dbg = vi.spyOn(console, 'debug').mockImplementation(() => {})
    renderMonitor()
    await connect()
    act(() => socket.emit({ type: 'brand_new_event_xyz' }))
    expect(dbg).toHaveBeenCalled()
    expect(screen.getByText('Agent Activity')).toBeInTheDocument()
  })

  it('treats agent_waiting as connected-but-not-started, not an error', async () => {
    api.getAgentStatus.mockResolvedValue(RUNNING)
    renderMonitor()
    await connect()
    act(() => socket.emit({ type: 'agent_waiting' }))
    expect(await screen.findByText(/waiting for the agent to start/i)).toBeInTheDocument()
    expect(screen.queryByText(/^❌/)).not.toBeInTheDocument()
    // the socket stays open
    expect(socket.close).not.toHaveBeenCalled()
  })

  it('does not duplicate events that were already loaded from the activity log (WS replay)', async () => {
    const logged = { type: 'chat_reply', content: 'Replay me once', timestamp: '2026-03-20T10:00:00' }
    api.getAgentActivity.mockResolvedValue({
      success: true,
      events: [{ created_at: '2026-03-20 10:00:00', type: 'chat_reply', data: logged }],
    })
    renderMonitor()
    expect(await screen.findByText(/Replay me once/)).toBeInTheDocument()
    await connect()
    // the server replays its backlog right after connect
    act(() => socket.emit(logged))
    expect(screen.getAllByText(/Replay me once/)).toHaveLength(1)
  })

  it('still shows a genuinely new event with the same text after the replay window', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const logged = { type: 'chat_reply', content: 'Same text', timestamp: '2026-03-20T10:00:00' }
      api.getAgentActivity.mockResolvedValue({
        success: true,
        events: [{ created_at: '2026-03-20 10:00:00', type: 'chat_reply', data: logged }],
      })
      renderMonitor()
      expect(await screen.findByText(/Same text/)).toBeInTheDocument()
      await connect()
      act(() => { vi.advanceTimersByTime(3500) })
      act(() => socket.emit({ ...logged, timestamp: '2026-03-20T11:00:00' }))
      expect(screen.getAllByText(/Same text/)).toHaveLength(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps live events when the activity fetch resolves after them (merge, not overwrite)', async () => {
    let resolveFetch
    api.getAgentActivity.mockImplementation(() => new Promise((r) => { resolveFetch = r }))
    renderMonitor()
    await connect()
    act(() => socket.emit({ type: 'chat_reply', content: 'Live while fetching' }))
    expect(await screen.findByText(/Live while fetching/)).toBeInTheDocument()
    await act(async () => {
      resolveFetch({ success: true, events: [{ created_at: '2026-03-20 10:00:00', type: 'cycle_end', data: { type: 'cycle_end', cycle: 1 } }] })
    })
    expect(screen.getByText(/Live while fetching/)).toBeInTheDocument()
    expect(await screen.findByText(/Task #1 completed/)).toBeInTheDocument()
  })

  it('shows an inline error and a retry when the activity log cannot be loaded', async () => {
    api.getAgentActivity.mockResolvedValue({ success: false, error: 'HTTP 500' })
    renderMonitor()
    expect(await screen.findByText(/Couldn't load past activity: HTTP 500/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('shows thinking below (older than) the reply it belongs to when streamed chunks flush', async () => {
    renderMonitor()
    await connect()
    act(() => {
      socket.emit({ type: 'chat_thinking', content: 'THINK-PART', chat_id: 'c' })
      socket.emit({ type: 'chat_content', content: 'REPLY-PART', chat_id: 'c' })
      socket.emit({ type: 'user_message', content: 'flush now', sender: 'Me' })
    })
    const thinking = await screen.findByText(/THINK-PART/)
    const reply = await screen.findByText(/REPLY-PART/)
    // newest-first list: the reply is rendered above its thinking
    expect(reply.compareDocumentPosition(thinking) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps the agent running (socket open) when Stop fails, and shows the error', async () => {
    const user = userEvent.setup()
    api.stopAutonomousAgent.mockResolvedValue({ success: false, error: 'Agent is busy' })
    renderMonitor()
    await connect()
    await user.click(await screen.findByRole('button', { name: /Stop Agent/ }))
    expect(await screen.findByText('Agent is busy')).toBeInTheDocument()
    expect(socket.close).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Stop Agent/ })).toBeInTheDocument()
  })

  it('closes the socket and flips to stopped after a successful Stop', async () => {
    const user = userEvent.setup()
    renderMonitor()
    await connect()
    await user.click(await screen.findByRole('button', { name: /Stop Agent/ }))
    expect(await screen.findByRole('button', { name: /Start Agent/ })).toBeInTheDocument()
    expect(socket.close).toHaveBeenCalled()
  })

  it('a failed status poll does not mark the agent as stopped', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderMonitor()
      expect(await screen.findByRole('button', { name: /Stop Agent/ })).toBeInTheDocument()
      api.getAgentStatus.mockResolvedValue({ success: false, error: 'HTTP 502' })
      await act(async () => { vi.advanceTimersByTime(5500) })
      expect(screen.getByRole('button', { name: /Stop Agent/ })).toBeInTheDocument()
      // an explicit "not running" answer does flip it
      api.getAgentStatus.mockResolvedValue({ success: true, running: false })
      await act(async () => { vi.advanceTimersByTime(5500) })
      await waitFor(() => expect(screen.getByRole('button', { name: /Start Agent/ })).toBeInTheDocument())
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not pop the startup modal for a stale startup_progress replay while running', async () => {
    renderMonitor()
    await connect()
    await screen.findByRole('button', { name: /Stop Agent/ })
    act(() => socket.emit({ type: 'startup_progress', step: 3, total: 7, label: 'Initialising memory' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the startup modal (as a dialog) for a start this tab initiated', async () => {
    const user = userEvent.setup()
    api.getAgentStatus.mockResolvedValue({ success: true, running: false })
    let resolveStart
    api.startAutonomousAgent.mockImplementation(() => new Promise((r) => { resolveStart = r }))
    renderMonitor()
    await user.click(await screen.findByRole('button', { name: /Start Agent/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByText('Starting Agent...')).toBeInTheDocument()
    await act(async () => { resolveStart({ success: false, error: 'bad key' }) })
    expect(await within(dialog).findByText('bad key')).toBeInTheDocument()
    // Escape closes a finished/failed modal
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('surfaces triggerAnalysis failures inline instead of alert()', async () => {
    const user = userEvent.setup()
    api.getScheduledTasks.mockResolvedValue({
      success: true,
      tasks: [{ id: 1, cron_expr: '0 8 * * *', prompt_goal: 'Morning review', status: 'active' }],
    })
    api.triggerAnalysis.mockResolvedValue({ success: false, error: 'queue full' })
    renderMonitor()
    await connect()
    await user.click(await screen.findByRole('button', { name: 'Run now' }))
    expect(await screen.findByText('queue full')).toBeInTheDocument()
    expect(globalThis.alert).not.toHaveBeenCalled()
  })
})
