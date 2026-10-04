/**
 * Run grouping for the Agent Monitor timeline (pure functions).
 */
import {
  buildTimeline, currentStep, filterByThread, findLiveRun, formatDuration, mergeChrono, resultText, stepObject,
  toTimelineRecord,
} from '../pages/runTimeline'

let n = 0
/** Build a chronological record list; ts advances 1s per event unless given. */
function recs(...events) {
  let ts = 1_000_000
  return events.map((e) => {
    const { type, ts: at, ...d } = e
    ts = at ?? ts + 1000
    return toTimelineRecord(type, d, { id: `r${n++}`, key: `${type}:${ts}:${n}`, ts, note: d.note })
  })
}
const runs = (items) => items.filter((i) => i.kind === 'run')
const NOW = 1_000_000 + 3_600_000

describe('toTimelineRecord', () => {
  it('drops event types that are raw-log only', () => {
    expect(toTimelineRecord('status_update', {}, { ts: 1 })).toBeNull()
    expect(toTimelineRecord('chat_thinking', {}, { ts: 1 })).toBeNull()
    expect(toTimelineRecord('chat_reply_delta', { text: 'x' }, { ts: 1 })).toBeNull()
    expect(toTimelineRecord('plan_tool_call', {}, { ts: 1 })).toMatchObject({ type: 'plan_tool_call' })
  })
})

describe('chat runs (by run_id)', () => {
  it('groups user message, steps and reply of one run_id and separates runs', () => {
    const items = buildTimeline(recs(
      { type: 'user_message', run_id: 'A', content: 'How did I sleep?', sender: 'Me' },
      { type: 'chat_tool_call', run_id: 'A', tool: 'analyze', call_id: 'c1', arguments: { goal: 'sleep last night' } },
      { type: 'chat_tool_result', run_id: 'A', tool: 'analyze', call_id: 'c1', success: true, status: 'ok', result_preview: 'ok' },
      { type: 'chat_reply', run_id: 'A', content: 'You slept 7h.', message_hash: 'h1' },
      { type: 'user_message', run_id: 'B', content: 'thanks' },
      { type: 'chat_reply', run_id: 'B', content: 'You are welcome' },
    ), { now: NOW, idle: true })
    const [a, b] = runs(items)
    expect(runs(items)).toHaveLength(2)
    expect(a.runId).toBe('A')
    expect(a.user.content).toBe('How did I sleep?')
    expect(a.segments.map((s) => s.kind)).toEqual(['steps', 'reply'])
    expect(a.segments[0].entries[0]).toMatchObject({ tool: 'analyze', status: 'ok', preview: 'ok' })
    expect(a.segments[1]).toMatchObject({ content: 'You slept 7h.', hash: 'h1' })
    expect(b.runId).toBe('B')
    expect(b.segments.map((s) => s.kind)).toEqual(['reply'])
  })

  it('nests sub-agent steps (parent tag) under the analyze / manage step and hides reply_user', () => {
    const [run] = runs(buildTimeline(recs(
      { type: 'user_message', run_id: 'A', content: 'q' },
      { type: 'chat_tool_call', run_id: 'A', tool: 'analyze', call_id: 'p', arguments: { goal: 'g' } },
      { type: 'chat_tool_call', run_id: 'A', tool: 'sql', call_id: 's1', parent: 'analyze', arguments: { query: 'SELECT 1' } },
      { type: 'chat_tool_result', run_id: 'A', tool: 'sql', call_id: 's1', parent: 'analyze', success: true, status: 'ok', result: { columns: ['a'], rows: [[1]] } },
      { type: 'chat_tool_call', run_id: 'A', tool: 'code', call_id: 's2', parent: 'analyze', arguments: { code: 'print(1)' } },
      { type: 'chat_tool_result', run_id: 'A', tool: 'code', call_id: 's2', parent: 'analyze', success: false, status: 'error', result: { error: 'NameError' } },
      { type: 'chat_tool_result', run_id: 'A', tool: 'analyze', call_id: 'p', success: true, status: 'ok' },
      { type: 'chat_tool_call', run_id: 'A', tool: 'reply_user', call_id: 'r', arguments: { message: 'hi' } },
      { type: 'chat_tool_result', run_id: 'A', tool: 'reply_user', call_id: 'r', success: true, status: 'ok' },
    ), { now: NOW, idle: true }))
    const entries = run.segments[0].entries
    expect(entries).toHaveLength(1) // reply_user is not a step row
    expect(entries[0].children.map((c) => [c.tool, c.status])).toEqual([['sql', 'ok'], ['code', 'error']])
    expect(run.stepCount).toBe(3)
    expect(run.hiccups).toBe(1)
  })

  it('pairs results to calls by tool when call_id is absent', () => {
    const [run] = runs(buildTimeline(recs(
      { type: 'user_message', run_id: 'A', content: 'q' },
      { type: 'chat_tool_call', run_id: 'A', tool: 'sql', arguments: { query: 'SELECT 1' } },
      { type: 'chat_tool_result', run_id: 'A', tool: 'sql', success: true },
    ), { now: NOW, idle: true }))
    expect(run.stepIndex).toHaveLength(1)
    expect(run.stepIndex[0].status).toBe('ok')
  })

  it('chat_stopped ends the run and marks running steps stopped', () => {
    const items = buildTimeline(recs(
      { type: 'user_message', run_id: 'A', content: 'q' },
      { type: 'chat_tool_call', run_id: 'A', tool: 'analyze', call_id: 'p', arguments: { goal: 'g' } },
      { type: 'chat_tool_call', run_id: 'A', tool: 'sql', call_id: 's', parent: 'analyze', arguments: { query: 'SELECT 1' } },
      { type: 'chat_stopped', run_id: 'A' },
    ), { now: 1_000_000 + 5000 })
    const [run] = runs(items)
    expect(run.status).toBe('stopped')
    expect(run.stepIndex.map((s) => s.status)).toEqual(['stopped', 'stopped'])
    expect(findLiveRun(items)).toBeNull()
  })

  it('a run without an end marker stays live until the agent is idle, then finishes', () => {
    const r = recs(
      { type: 'user_message', run_id: 'A', content: 'q', ts: 1_000_000 },
      { type: 'chat_tool_call', run_id: 'A', tool: 'sql', call_id: 's', arguments: { query: 'SELECT 1' }, ts: 1_001_000 },
    )
    const busy = buildTimeline(r, { now: 1_003_000, idle: false })
    expect(findLiveRun(busy)?.runId).toBe('A')
    expect(currentStep(findLiveRun(busy))?.tool).toBe('sql')
    const idle = buildTimeline(r, { now: 1_020_000, idle: true })
    expect(findLiveRun(idle)).toBeNull()
    expect(idle[0].status).toBe('done')
    // an old run the agent never closed is "interrupted", not live forever
    expect(buildTimeline(r, { now: 1_001_000 + 16 * 60 * 1000, idle: false })[0].status).toBe('interrupted')
  })

  it('demotes narration to a thought row and attaches token totals', () => {
    const [run] = runs(buildTimeline(recs(
      { type: 'user_message', run_id: 'A', content: 'q' },
      { type: 'thought', run_id: 'A', scope: 'chat', content: 'Let me look at your sleep.' },
      { type: 'chat_tool_call', run_id: 'A', tool: 'sql', call_id: 's', arguments: { query: 'SELECT 1' } },
      { type: 'token_usage', chat_id: 'c', prompt_tokens: 100, response_tokens: 20, thoughts_tokens: 5 },
      { type: 'token_usage', chat_id: 'c', prompt_tokens: 50, completion_tokens: 10 },
    ), { now: NOW, idle: true }))
    expect(run.segments[0].entries.map((e) => e.kind)).toEqual(['thought', 'step'])
    expect(run.tokens).toEqual({ prompt: 150, response: 30, thoughts: 5 })
  })

  it('falls back to time-adjacent grouping for events without run_id', () => {
    const items = buildTimeline(recs(
      { type: 'user_message', content: 'first', ts: 1_000_000 },
      { type: 'chat_tool_call', tool: 'analyze', arguments: { goal: 'g' }, ts: 1_001_000 },
      { type: 'chat_tool_result', tool: 'analyze', success: true, ts: 1_002_000 },
      { type: 'chat_reply', content: 'answer 1', ts: 1_003_000 },
      { type: 'user_message', content: 'second', ts: 1_060_000 },
      { type: 'chat_reply', content: 'answer 2', ts: 1_061_000 },
    ), { now: NOW, idle: true })
    const [a, b] = runs(items)
    expect(runs(items)).toHaveLength(2)
    expect(a.segments.map((s) => s.kind)).toEqual(['steps', 'reply'])
    expect(b.user.content).toBe('second')
    expect(b.segments[0].content).toBe('answer 2')
  })

  it('chat_cleared becomes a notice and chat_image a segment', () => {
    const items = buildTimeline(recs(
      { type: 'user_message', run_id: 'A', content: 'plot' },
      { type: 'chat_image', run_id: 'A', caption: 'HR', image_id: 'abc' },
      { type: 'chat_cleared', note: 'Chat history cleared' },
    ), { now: NOW, idle: true })
    expect(runs(items)[0].segments[0]).toMatchObject({ kind: 'image', imageId: 'abc' })
    expect(items[items.length - 1]).toMatchObject({ kind: 'notice', text: 'Chat history cleared' })
  })
})

describe('background runs', () => {
  it('groups cycle_start..cycle_end, types the run and attaches the pushed report', () => {
    const items = buildTimeline(recs(
      { type: 'cycle_start', cycle: 1, goal: 'Morning sleep review' },
      { type: 'analysis_tool_call', tool: 'sql', arguments: { query: 'SELECT 1' }, cycle: 1 },
      { type: 'analysis_tool_result', tool: 'sql', success: true, cycle: 1 },
      { type: 'report_pushed', report_id: 42, cycle: 1 },
      { type: 'cycle_end', cycle: 1 },
      { type: 'cycle_start', cycle: 2, source: 'plan' },
      { type: 'plan_tool_call', tool: 'sql', arguments: { query: 'SELECT 2' } },
      { type: 'cycle_end', cycle: 2 },
    ), { now: NOW, idle: true })
    const [a, b] = runs(items)
    expect(a).toMatchObject({ runType: 'scheduled', goal: 'Morning sleep review', status: 'done' })
    expect(a.report).toMatchObject({ id: 42 })
    expect(a.stepIndex).toHaveLength(1)
    expect(b.runType).toBe('plan')
    expect(b.stepIndex[0].tool).toBe('sql')
  })

  it('groups quick analysis by its start/complete markers', () => {
    const [run] = runs(buildTimeline(recs(
      { type: 'quick_analysis_start' },
      { type: 'quick_tool_call', tool: 'sql', arguments: { query: 'SELECT 1' }, task: 'quick_analysis' },
      { type: 'quick_tool_result', tool: 'sql', success: true, task: 'quick_analysis' },
      { type: 'quick_analysis_complete', state: 'relaxed' },
    ), { now: NOW, idle: true }))
    expect(run).toMatchObject({ runType: 'quick', status: 'done', quickState: 'relaxed' })
    expect(run.stepIndex).toHaveLength(1)
  })

  it('without cycle markers, time-adjacent tool events form one implicit run (then a gap splits it)', () => {
    const items = buildTimeline(recs(
      { type: 'analysis_tool_call', tool: 'sql', arguments: {}, ts: 1_000_000 },
      { type: 'analysis_tool_result', tool: 'sql', success: true, ts: 1_001_000 },
      { type: 'analysis_tool_call', tool: 'code', arguments: {}, ts: 1_002_000 },
      { type: 'analysis_tool_call', tool: 'sql', arguments: {}, ts: 1_002_000 + 6 * 60 * 1000 },
    ), { now: NOW, idle: true })
    const rs = runs(items)
    expect(rs).toHaveLength(2)
    expect(rs[0].stepIndex.map((s) => s.tool)).toEqual(['sql', 'code'])
  })

  it('a report with no open run becomes a notice with a report link target', () => {
    const items = buildTimeline(recs({ type: 'report_pushed', report_id: 7, note: 'Report pushed (ID: 7)' }), { now: NOW, idle: true })
    expect(items[0]).toMatchObject({ kind: 'notice', reportId: 7 })
  })

  it('agent_stopped closes open runs; agent_error marks them failed', () => {
    const stopped = buildTimeline(recs(
      { type: 'cycle_start', cycle: 1, goal: 'g' },
      { type: 'agent_stopped' },
    ), { now: 1_003_000 })
    expect(runs(stopped)[0].status).toBe('stopped')
    const failed = buildTimeline(recs(
      { type: 'cycle_start', cycle: 1, goal: 'g' },
      { type: 'agent_error', error: 'x', note: 'boom' },
    ), { now: 1_003_000 })
    expect(runs(failed)[0].status).toBe('error')
    expect(failed[failed.length - 1]).toMatchObject({ kind: 'notice', tone: 'bad', text: 'boom' })
  })
})

describe('mergeChrono', () => {
  it('dedupes by key, keeps live-only records, sorts oldest first and caps', () => {
    const hist = [{ key: 'a', ts: 1 }, { key: 'b', ts: 2 }]
    const live = [{ key: 'b', ts: 2 }, { key: 'c', ts: 3 }, { key: null, ts: 2.5 }]
    expect(mergeChrono(live, hist).map((r) => r.key)).toEqual(['a', 'b', null, 'c'])
    expect(mergeChrono(live, hist, 2).map((r) => r.key)).toEqual([null, 'c'])
  })

  it('counts duplicates: two identical history events and one live copy leave two', () => {
    const hist = [{ key: 'x', ts: 1 }, { key: 'x', ts: 1 }]
    expect(mergeChrono([{ key: 'x', ts: 1 }], hist)).toHaveLength(2)
  })
})

describe('helpers', () => {
  it('stepObject picks the right argument per tool, falling back to summary', () => {
    expect(stepObject({ tool: 'sql', args: { query: '\n SELECT  a\nFROM t' } })).toBe('SELECT a')
    expect(stepObject({ tool: 'analyze', args: { goal: 'sleep   trend' } })).toBe('sleep trend')
    expect(stepObject({ tool: 'weird', args: {}, summary: 'did a thing' })).toBe('did a thing')
  })

  it('resultText unwraps output/content/text from JSON and objects', () => {
    expect(resultText({ output: 'hello' })).toBe('hello')
    expect(resultText('{"content":"from json"}')).toBe('from json')
    expect(resultText({ text: 'T' })).toBe('T')
    expect(resultText('plain')).toBe('plain')
    expect(resultText({}, 'preview only')).toBe('preview only')
    expect(resultText(undefined, 'p')).toBe('p')
  })

  it('formatDuration', () => {
    expect(formatDuration(400)).toBe('0.4s')
    expect(formatDuration(1500)).toBe('1.5s')
    expect(formatDuration(45_000)).toBe('45s')
    expect(formatDuration(125_000)).toBe('2m 5s')
    expect(formatDuration(null)).toBeNull()
  })
})

describe('chat threads', () => {
  const T1 = 'a'.repeat(32)
  const items = () => buildTimeline(recs(
    { type: 'user_message', run_id: 'A', content: 'main q' },
    { type: 'chat_reply', run_id: 'A', content: 'main a', thread_id: 'main' },
    { type: 'user_message', run_id: 'B', content: 'thread q', thread_id: T1 },
    { type: 'chat_reply', run_id: 'B', content: 'thread a', thread_id: T1 },
    { type: 'cycle_start', cycle: 1, goal: 'daily' },
    { type: 'cycle_end', cycle: 1 },
  ), { now: NOW, idle: true })

  it('records the thread id on chat runs', () => {
    const chat = runs(items()).filter((r) => r.runType === 'chat')
    expect(chat.map((r) => r.threadId)).toEqual(['main', T1])
  })

  it('filters by thread; background runs belong to main', () => {
    const all = items()
    expect(filterByThread(all, 'all')).toBe(all)
    const t1 = filterByThread(all, T1)
    expect(runs(t1).map((r) => r.runType)).toEqual(['chat'])
    const main = runs(filterByThread(all, 'main'))
    expect(main.some((r) => r.runType !== 'chat')).toBe(true)
    expect(main.filter((r) => r.runType === 'chat')).toHaveLength(1)
  })
})
