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
    // the plan run shows up as a card with its step (also echoed by the status hero)
    expect((await screen.findAllByText(/SELECT plan_marker FROM t/)).length).toBeGreaterThan(0)
    expect(screen.getByText('Plan')).toBeInTheDocument()
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
      resolveFetch({ success: true, events: [{ created_at: '2026-03-20 10:00:00', type: 'agent_error', data: { type: 'agent_error', error: 'boom-from-history' } }] })
    })
    expect(screen.getByText(/Live while fetching/)).toBeInTheDocument()
    expect(await screen.findByText(/boom-from-history/)).toBeInTheDocument()
  })

  it('shows an inline error and a retry when the activity log cannot be loaded', async () => {
    api.getAgentActivity.mockResolvedValue({ success: false, error: 'HTTP 500' })
    renderMonitor()
    expect(await screen.findByText(/Couldn't load past activity: HTTP 500/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('raw log: shows thinking below (older than) the reply it belongs to when streamed chunks flush', async () => {
    const user = userEvent.setup()
    renderMonitor()
    await connect()
    await user.click(screen.getByRole('button', { name: 'Raw log' }))
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
  // ── Run timeline ────────────────────────────────────────────────────────
  describe('run timeline', () => {
    const run = (extra) => ({ run_id: 'r1', chat_id: 'ios:LiveUser', ...extra })

    it('shows a friendly empty state when there is no activity', async () => {
      api.getAgentStatus.mockResolvedValue({ success: true, running: false })
      renderMonitor()
      expect(await screen.findByText('Hime is idle')).toBeInTheDocument()
      expect(screen.getByText(/send a message from the app/i)).toBeInTheDocument()
      expect(screen.getByTestId('status-hero')).toHaveTextContent('Stopped')
    })

    it('renders a live chat run: user bubble, step, status hero, live bubble and Stop reply', async () => {
      const user = userEvent.setup()
      renderMonitor()
      await connect()
      act(() => {
        socket.emit(run({ type: 'user_message', content: 'How did I sleep?', sender: 'Me' }))
        socket.emit(run({ type: 'chat_tool_call', tool: 'analyze', call_id: 'a', arguments: { goal: 'sleep last night' } }))
        socket.emit(run({ type: 'chat_tool_call', tool: 'sql', call_id: 's', parent: 'analyze', arguments: { query: 'SELECT hr FROM samples' } }))
      })
      expect(await screen.findByText('How did I sleep?')).toBeInTheDocument()
      expect(screen.getByTestId('status-hero')).toHaveTextContent('Working on: Looking through your data')
      expect(screen.getByTestId('live-bubble')).toBeInTheDocument()
      // the nested sql step is indented under analyze, both visible while the run is live
      expect(screen.getByText('Analysing your data')).toBeInTheDocument()
      // Stop reply calls the chat-stop endpoint
      await user.click(screen.getByRole('button', { name: 'Stop reply' }))
      expect(api.stopChat).toHaveBeenCalledTimes(1)
    })

    it('chat_stopped quietly ends the live bubble and marks the steps stopped', async () => {
      renderMonitor()
      await connect()
      act(() => {
        socket.emit(run({ type: 'user_message', content: 'long question' }))
        socket.emit(run({ type: 'chat_tool_call', tool: 'sql', call_id: 's', arguments: { query: 'SELECT 1' } }))
      })
      expect(await screen.findByRole('button', { name: 'Stop reply' })).toBeInTheDocument()
      act(() => socket.emit(run({ type: 'chat_stopped' })))
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop reply' })).not.toBeInTheDocument())
      expect(screen.queryByTestId('live-bubble')).not.toBeInTheDocument()
      expect(screen.getByText('Reply stopped.')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Stopped · 1 step/ })).toBeInTheDocument()
    })

    it('streams chat_reply_delta into a bubble with a caret and replaces it with chat_reply', async () => {
      renderMonitor()
      await connect()
      act(() => {
        socket.emit(run({ type: 'user_message', content: 'hi' }))
        socket.emit(run({ type: 'chat_reply_delta', text: 'You slep' }))
      })
      expect(await screen.findByTestId('streaming-reply')).toHaveTextContent('You slep')
      act(() => socket.emit(run({ type: 'chat_reply_delta', text: 'You slept 7h' })))
      expect(screen.getByTestId('streaming-reply')).toHaveTextContent('You slept 7h')
      // reset clears a draft that was never delivered
      act(() => socket.emit(run({ type: 'chat_reply_delta', text: '', reset: true })))
      expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument()
      act(() => socket.emit(run({ type: 'chat_reply_delta', text: 'You slept 7h 20m' })))
      expect(screen.getByTestId('streaming-reply')).toBeInTheDocument()
      act(() => socket.emit(run({ type: 'chat_reply', content: 'You slept 7h 20m.', message_hash: 'h' })))
      expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument()
      expect(screen.getAllByText(/You slept 7h 20m\./)).toHaveLength(1)
    })

    it('groups persisted history into runs by run_id, with expandable step results', async () => {
      const user = userEvent.setup()
      const ev = (type, data, at) => ({ created_at: at, type, data: { type, ...data } })
      api.getAgentStatus.mockResolvedValue({ success: true, running: false })
      api.getAgentActivity.mockResolvedValue({
        success: true,
        events: [
          ev('user_message', { run_id: 'h1', content: 'First question' }, '2026-03-20 10:00:00'),
          ev('chat_tool_call', { run_id: 'h1', tool: 'sql', call_id: 'c', arguments: { query: 'SELECT night FROM sleep' } }, '2026-03-20 10:00:01'),
          ev('chat_tool_result', { run_id: 'h1', tool: 'sql', call_id: 'c', success: true, status: 'ok', result: { columns: ['night', 'hours'], rows: [['mon', 7.5]] } }, '2026-03-20 10:00:02'),
          ev('chat_reply', { run_id: 'h1', content: 'Answer **one**' }, '2026-03-20 10:00:03'),
          ev('user_message', { run_id: 'h2', content: 'Second question' }, '2026-03-20 10:05:00'),
          ev('chat_reply', { run_id: 'h2', content: 'Answer two' }, '2026-03-20 10:05:01'),
        ],
      })
      renderMonitor()
      const first = await screen.findByText('First question')
      const second = screen.getByText('Second question')
      // oldest first: the second question comes after the first run
      expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(screen.getByText('one').tagName).toBe('STRONG') // markdown rendered
      // finished run: collapsed card with a plain-language summary
      const header = screen.getByRole('button', { name: /Finished · 1 step/ })
      expect(header).toHaveAttribute('aria-expanded', 'false')
      await user.click(header)
      await user.click(screen.getByRole('button', { name: /Looking through your data/ }))
      expect(screen.getByRole('columnheader', { name: 'hours' })).toBeInTheDocument()
      expect(screen.getByText('7.5')).toBeInTheDocument()
    })

    it('renders a background run with its badge, steps and a Report pushed link', async () => {
      renderMonitor()
      await connect()
      act(() => {
        socket.emit({ type: 'cycle_start', cycle: 3, goal: 'Daily sleep analysis' })
        socket.emit({ type: 'analysis_tool_call', tool: 'sql', arguments: { query: 'SELECT 1' }, cycle: 3 })
        socket.emit({ type: 'analysis_tool_result', tool: 'sql', success: true, result: { columns: ['a'], rows: [[1]] }, cycle: 3 })
        socket.emit({ type: 'report_pushed', report_id: 12, cycle: 3 })
        socket.emit({ type: 'cycle_end', cycle: 3 })
      })
      expect(await screen.findByText('Scheduled')).toBeInTheDocument()
      expect(screen.getByText('Daily sleep analysis')).toBeInTheDocument()
      expect(screen.getByText('Report pushed')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'View reports' })).toHaveAttribute('href', '/reports')
    })

    it('keeps the raw log available as a separate view', async () => {
      const user = userEvent.setup()
      renderMonitor()
      await connect()
      act(() => socket.emit({ type: 'chat_reply', content: 'visible in both views' }))
      expect(await screen.findByText(/visible in both views/)).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Raw log' }))
      expect(screen.getByRole('button', { name: 'Raw log' })).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByText(/🤖 visible in both views/)).toBeInTheDocument()
    })
  })
})
