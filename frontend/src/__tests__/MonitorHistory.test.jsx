/**
 * Agent Monitor against the *shape* of real persisted activity (anonymised,
 * synthetic content): legacy events without run_id, report replies announced
 * as chat_reply, trailing tool results after cycle_end, held-back drafts. Also
 * replays a live WS sequence and checks nothing jumps or vanishes.
 */
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../lib/api', () => import('../test/mocks/api'))

import AutonomousAgentMonitor from '../pages/AutonomousAgentMonitor'
import { AppProvider } from '../context/AppContext'
import { api } from '../test/mocks/api'
import { buildTimeline, toTimelineRecord } from '../pages/runTimeline'

function makeFakeSocket() {
  const ws = {
    onopen: null, onmessage: null, onclose: null, onerror: null,
    close: vi.fn(), send: vi.fn(),
    emit(obj) { ws.onmessage?.({ data: JSON.stringify(obj) }) },
  }
  return ws
}

const ev = (created_at, type, data = {}) => ({ created_at, type, data: { type, ...data } })

/** One scheduled cycle exactly as persisted: tool result trails cycle_end, report text is a run_id-less chat_reply. */
function cronCycle(day, hh, n) {
  const t = (mm, ss) => `${day}T${hh}:${mm}:${ss}`
  return [
    ev(t('00', '00'), 'cycle_start', { cycle: n, goal: `Daily check ${n}`, timestamp: t('00', '00') }),
    ev(t('00', '01'), 'analysis_tool_call', { tool: 'sql', arguments: { query: 'SELECT 1' }, source: 'analysis' }),
    ev(t('00', '01'), 'tool_progress', { tool: 'sql', data: 'x' }),
    ev(t('00', '02'), 'analysis_tool_result', { tool: 'sql', success: true, result: { columns: ['a'], rows: [[1]] } }),
    ev(t('00', '05'), 'analysis_tool_call', { tool: 'push_report', arguments: { title: `Report ${n}` } }),
    ev(t('00', '06'), 'chat_verification', { tool: 'push_report', status: 'verified', preview: `Report body ${n}` }),
    ev(t('00', '06'), 'chat_reply', { content: `Report body ${n}`, chat_id: 'u', final: true }),
    ev(t('00', '06'), 'cycle_end', { cycle: n }),
    ev(t('00', '06'), 'analysis_tool_result', { tool: 'push_report', success: true, result: {} }),
    ev(t('00', '06'), 'report_pushed', { cycle: n, report_id: n }),
  ]
}

describe('buildTimeline on persisted-activity shapes', () => {
  const recs = (events) => events
    .map((e, i) => toTimelineRecord(e.type, e.data, { id: i, ts: Date.parse(`${e.created_at}Z`) }))
    .filter(Boolean)

  it('folds a scheduled cycle (incl. trailing result and report reply) into ONE block with its report text', () => {
    const items = buildTimeline(recs(cronCycle('2026-09-17', '09', 7)), { now: Date.parse('2026-10-01T00:00:00Z'), idle: true })
    expect(items).toHaveLength(1)
    expect(items[0].runType).toBe('scheduled')
    expect(items[0].reply.content).toBe('Report body 7')
    expect(items[0].report.id).toBe(7)
    expect(items[0].status).toBe('done')
    expect(items[0].stepIndex).toHaveLength(2)
  })

  it('keeps ids stable when older history is prepended (no remount of later blocks)', () => {
    const late = recs(cronCycle('2026-09-18', '09', 8))
    const alone = buildTimeline(late, { now: Date.parse('2026-10-01T00:00:00Z'), idle: true })
    const withOlder = buildTimeline([...recs(cronCycle('2026-09-17', '09', 7)), ...late], { now: Date.parse('2026-10-01T00:00:00Z'), idle: true })
    expect(withOlder.map((i) => i.id)).toContain(alone[0].id)
  })

  it('shows a fact-check hold as a held-back draft, not as a reply, and merges the reset reason into it', () => {
    const base = Date.parse('2026-10-04T16:00:00Z')
    const r = (type, d, dt) => toTimelineRecord(type, { run_id: 'r1', ...d }, { id: dt, ts: base + dt * 1000 })
    const items = buildTimeline([
      r('user_message', { content: 'hello' }, 0),
      r('chat_verification', { tool: 'reply_user', status: 'unverified', preview: 'draft one', detail: 'claims 3 nights' }, 2),
      r('draft_held', { content: 'draft one', reason: 'verification' }, 3),
      r('chat_verification', { tool: 'reply_user', status: 'verified', preview: 'final text' }, 8),
      r('chat_reply', { content: 'final text', final: true }, 9),
    ], { now: base + 60000, idle: true })
    const run = items[0]
    const drafts = run.segments.flatMap((s) => (s.kind === 'steps' ? s.entries.filter((e) => e.kind === 'draft') : []))
    expect(drafts).toHaveLength(1)
    expect(drafts[0]).toMatchObject({ text: 'draft one', reason: 'verification', detail: 'claims 3 nights' })
    expect(run.segments.filter((s) => s.kind === 'reply')).toHaveLength(1)
    expect(run.status).toBe('done')
  })

  it('drops a "held" draft that was in the end delivered unchanged', () => {
    const base = Date.parse('2026-10-04T16:00:00Z')
    const r = (type, d, dt) => toTimelineRecord(type, { run_id: 'r1', ...d }, { id: dt, ts: base + dt * 1000 })
    const items = buildTimeline([
      r('user_message', { content: 'hi' }, 0),
      r('chat_verification', { tool: 'reply_user', status: 'unverified', preview: 'same text' }, 1),
      r('chat_reply', { content: 'same  text', final: true }, 2),
    ], { now: base + 60000, idle: true })
    expect(items[0].segments.some((s) => s.kind === 'steps')).toBe(false)
  })

  it('a run stays one block when steps follow an acknowledgement reply', () => {
    const base = Date.parse('2026-10-04T16:00:00Z')
    const r = (type, d, dt) => toTimelineRecord(type, { run_id: 'r2', ...d }, { id: dt, ts: base + dt * 1000 })
    const items = buildTimeline([
      r('user_message', { content: 'q' }, 0),
      r('chat_reply', { content: 'On it', final: true }, 1),
      r('chat_tool_call', { tool: 'analyze', arguments: { goal: 'g' } }, 2),
    ], { now: base + 3000, idle: false })
    expect(items).toHaveLength(1)
    expect(items[0].status).toBe('running')
  })
})

describe('AutonomousAgentMonitor with persisted history', () => {
  let socket
  beforeEach(() => {
    socket = makeFakeSocket()
    api.connectAgentMonitor.mockImplementation(() => socket)
    api.getAgentStatus.mockResolvedValue({ success: true, running: true, config: {}, status: { state: 'idle' } })
    api.getDefaults.mockResolvedValue({ success: true })
    api.getAgentLastConfig.mockResolvedValue({ success: true, config: {} })
    api.getScheduledTasks.mockResolvedValue({ success: true, tasks: [] })
    api.getTriggerRules.mockResolvedValue({ success: true, rules: [] })
    api.getStreamConfig.mockResolvedValue({ success: true, live_history_window: '1hour' })
    api.getFeatureTypes.mockResolvedValue({ success: true, feature_types: [] })
    api.fetchChatImage.mockResolvedValue({ success: false })
    api.connectDataStream.mockImplementation(() => makeFakeSocket())
  })

  const mount = () => render(<AppProvider><AutonomousAgentMonitor active /></AppProvider>)
  const connect = async () => {
    await waitFor(() => expect(api.connectAgentMonitor).toHaveBeenCalled())
    act(() => { socket.onopen?.(new Event('open')) })
  }
  const blocks = (c) => c.querySelectorAll('section')

  it('renders every historic day as one block per run, oldest first, with day separators and lazy "show earlier"', async () => {
    const events = []
    // 40 days x 2 cycles = 80 blocks (> one page of 60) + one legacy chat on day 1
    for (let d = 1; d <= 40; d += 1) {
      const day = new Date(Date.UTC(2026, 7, d)).toISOString().slice(0, 10)
      events.push(...cronCycle(day, '09', d * 2), ...cronCycle(day, '19', d * 2 + 1))
      if (d === 1) {
        events.push(
          ev(`${day}T12:00:00`, 'user_message', { content: 'How did I sleep?', sender: 's' }),
          ev(`${day}T12:00:05`, 'chat_tool_call', { tool: 'analyze', arguments: { goal: 'sleep' } }),
          ev(`${day}T12:00:30`, 'chat_tool_result', { tool: 'analyze', success: true, result: {} }),
          ev(`${day}T12:00:31`, 'chat_reply', { content: 'You slept well.', final: true }),
        )
      }
    }
    api.getAgentActivity.mockResolvedValue({ success: true, events })
    const { container } = mount()
    // newest page rendered, the oldest hidden behind a button
    const btn = await screen.findByRole('button', { name: /earlier/i })
    expect(blocks(container).length).toBe(60)
    expect(screen.queryByText('You slept well.')).not.toBeInTheDocument()
    await userEvent.click(btn)
    // history is fully reachable: all 81 runs (80 cycles + 1 chat)
    expect(blocks(container).length).toBe(81)
    expect(screen.getByText('You slept well.')).toBeInTheDocument()
    expect(screen.getByText('How did I sleep?')).toBeInTheDocument()
    // a report is shown once (as the run's reply), never duplicated as a phantom chat block
    expect(screen.getAllByText('Report body 81').length).toBe(1)
    expect(container.querySelectorAll('[role=separator]').length).toBeGreaterThanOrEqual(40)
  })

  it('live: user message -> narration -> analyze with nested calls -> streamed reply blocked -> second reply -> final', async () => {
    api.getAgentActivity.mockResolvedValue({ success: true, events: [] })
    const { container } = mount()
    await connect()
    const run = 'run-live-1'
    const emit = (o) => act(() => socket.emit({ run_id: run, chat_id: 'ios:LiveUser', ...o }))

    emit({ type: 'user_message', content: 'Is my sleep ok?', sender: 'me' })
    expect(await screen.findByText('Is my sleep ok?')).toBeInTheDocument()
    expect(blocks(container).length).toBe(1)

    emit({ type: 'chat_content', content: 'Let me look.' })
    emit({ type: 'chat_tool_call', tool: 'analyze', call_id: 'a1', arguments: { goal: 'check sleep last week' } })
    emit({ type: 'chat_tool_call', tool: 'sql', parent: 'analyze', call_id: 's1', arguments: { query: 'SELECT sleep' } })
    emit({ type: 'chat_tool_result', tool: 'sql', parent: 'analyze', call_id: 's1', success: true, result: { columns: ['a'], rows: [[1]] } })
    emit({ type: 'chat_tool_result', tool: 'analyze', call_id: 'a1', success: true, result: {} })
    expect(blocks(container).length).toBe(1) // still the same run block
    expect(screen.getByText(/check sleep last week/)).toBeInTheDocument()

    // streamed reply, then blocked: the draft must not simply vanish
    emit({ type: 'chat_reply_delta', text: 'You slept 9 hours' })
    expect(await screen.findByTestId('streaming-reply')).toHaveTextContent('You slept 9 hours')
    emit({ type: 'chat_tool_call', tool: 'reply_user', call_id: 'r1', arguments: { message: 'You slept 9 hours' } })
    emit({ type: 'chat_tool_result', tool: 'reply_user', call_id: 'r1', success: false, result: { error: 'blocked' } })
    emit({ type: 'chat_verification', tool: 'reply_user', status: 'unverified', preview: 'You slept 9 hours', detail: 'not in results' })
    emit({ type: 'chat_reply_delta', reset: true, reason: 'verification', detail: 'not in results' })
    await waitFor(() => expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument())
    expect(blocks(container).length).toBe(1)

    emit({ type: 'chat_reply_delta', text: 'You slept 6 hours' })
    expect(await screen.findByTestId('streaming-reply')).toHaveTextContent('You slept 6 hours')
    emit({ type: 'chat_reply', content: 'You slept 6 hours', final: true })
    await waitFor(() => expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument())
    expect(screen.getByText('You slept 6 hours')).toBeInTheDocument()
    expect(blocks(container).length).toBe(1)

    // the held-back draft is kept inside the run's (collapsed) activity, labelled
    const card = within(container.querySelector('section')).getAllByRole('button', { expanded: false })[0]
    await userEvent.click(card)
    expect(await screen.findByText('Draft held back by fact check')).toBeInTheDocument()
    // exactly one draft even though verification and reset both described it
    expect(screen.getAllByTestId('draft-held')).toHaveLength(1)
  })

  it('a replayed backlog after (re)connect does not swallow new events or duplicate old ones', async () => {
    const hist = [
      ev('2026-10-04T10:00:00', 'user_message', { content: 'old question', run_id: 'old' }),
      ev('2026-10-04T10:00:05', 'chat_reply', { content: 'old answer', run_id: 'old', final: true }),
    ]
    api.getAgentActivity.mockResolvedValue({ success: true, events: hist })
    const { container } = mount()
    await connect()
    await screen.findByText('old answer')
    // server replays its backlog, then a genuinely new message arrives
    act(() => {
      socket.emit({ type: 'user_message', content: 'old question', run_id: 'old' })
      socket.emit({ type: 'chat_reply', content: 'old answer', run_id: 'old', final: true })
      socket.emit({ type: 'user_message', content: 'new question', run_id: 'new' })
      socket.emit({ type: 'chat_reply', content: 'new answer', run_id: 'new', final: true })
    })
    expect(await screen.findByText('new answer')).toBeInTheDocument()
    expect(screen.getAllByText('old answer')).toHaveLength(1)
    expect(blocks(container).length).toBe(2)
  })
})
