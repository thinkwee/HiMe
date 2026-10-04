/**
 * eventToMessage coverage — every event type the backend emits must render
 * (or be deliberately ignored), never silently vanish.
 */
import {
  chatImageUrl, eventKey, eventTimeMs, eventToMessage, formatTokenUsage, mergeActivity,
} from '../pages/agentEvents'
import { parseBackendDate } from '../lib/utils'

describe('eventToMessage', () => {
  it('renders chat_reply as the assistant reply (chat badge)', () => {
    const m = eventToMessage({ type: 'chat_reply', content: 'Your sleep was fine.', chat_id: 'ios:LiveUser', final: true })
    expect(m).toMatchObject({ type: 'reply', taskType: 'chat' })
    expect(m.text).toContain('Your sleep was fine.')
  })

  it('ignores an empty chat_reply instead of logging a blank line', () => {
    expect(eventToMessage({ type: 'chat_reply', content: '' })).toBeNull()
  })

  it('renders chat_image with caption and the authed image path', () => {
    const m = eventToMessage({ type: 'chat_image', image_id: 'abc123', url: '/api/agent/chat-image/abc123', caption: 'HR chart' })
    expect(m).toMatchObject({ type: 'image', text: 'HR chart', imageUrl: '/api/agent/chat-image/abc123', taskType: 'chat' })
  })

  it('derives the image url from image_id when no url is sent, and tolerates neither', () => {
    expect(chatImageUrl({ image_id: 'a/b' })).toBe('/api/agent/chat-image/a%2Fb')
    expect(chatImageUrl({})).toBeNull()
    expect(eventToMessage({ type: 'chat_image', caption: '' }).text).toBe('Image')
  })

  it('renders chat_stopped and silently skips chat_reply_delta snapshots', () => {
    expect(eventToMessage({ type: 'chat_stopped', chat_id: 'x', run_id: 'r' })).toMatchObject({ type: 'system', taskType: 'chat' })
    expect(eventToMessage({ type: 'chat_reply_delta', text: 'partial', run_id: 'r' })).toBeNull()
  })

  it('renders chat_cleared and tool_progress', () => {
    expect(eventToMessage({ type: 'chat_cleared', chat_id: 'x' })).toMatchObject({ type: 'system', taskType: 'chat' })
    const p = eventToMessage({ type: 'tool_progress', tool: 'code', data: { pct: 50 } })
    expect(p).toMatchObject({ type: 'progress', progressTool: 'code' })
    expect(p.text).toContain('code')
    expect(p.text).toContain('50')
  })

  it.each(['chat', 'analysis', 'quick', 'plan'])('matches %s_tool_call / %s_tool_result generically', (prefix) => {
    const call = eventToMessage({ type: `${prefix}_tool_call`, tool: 'sql', arguments: { query: 'SELECT 1' } })
    expect(call).toMatchObject({ type: 'tool_call', toolName: 'sql', text: 'SELECT 1' })
    const res = eventToMessage({ type: `${prefix}_tool_result`, tool: 'code', success: true, result: { output: 'ok' } })
    expect(res).toMatchObject({ type: 'tool_result', toolName: 'code', toolSuccess: true, text: 'ok' })
  })

  it('tags plan events and quick events with their own badge', () => {
    expect(eventToMessage({ type: 'plan_tool_call', tool: 'sql', arguments: {} }).taskType).toBe('plan')
    expect(eventToMessage({ type: 'quick_tool_call', tool: 'sql', arguments: {} }).taskType).toBe('quick')
    // source === 'quick' without a quick_ type prefix
    expect(eventToMessage({ type: 'cycle_start', source: 'quick', cycle: 3 }).taskType).toBe('quick')
    expect(eventToMessage({ type: 'cycle_start', task: 'quick_analysis', cycle: 3 }).taskType).toBe('quick')
  })

  it('reports sql row counts via the (previously missing) i18n keys', () => {
    const m = eventToMessage({
      type: 'analysis_tool_result', tool: 'sql', success: true,
      result: { columns: ['a'], rows: [[1], [2]], row_count: 2, truncated: true },
    })
    expect(m.text).toBe('2 rows (truncated)')
    const m2 = eventToMessage({
      type: 'analysis_tool_result', tool: 'sql', success: true,
      result: { columns: ['a'], rows: [[1]] },
    })
    expect(m2.text).toBe('1 rows')
  })

  it('user_message falls back to a translated sender name', () => {
    expect(eventToMessage({ type: 'user_message', content: 'hi' }).text).toBe('User: hi')
    expect(eventToMessage({ type: 'user_message', sender: 'Ann', content: 'hi' }).text).toBe('Ann: hi')
  })

  it('does not claim auto-restart for a generic error frame', () => {
    const e = eventToMessage({ type: 'error', error: 'No active agent' })
    expect(e.type).toBe('error')
    expect(e.text).toContain('No active agent')
    expect(e.text.toLowerCase()).not.toContain('restart')
    // agent_error is the supervised-restart case
    expect(eventToMessage({ type: 'agent_error', error: 'boom' }).text.toLowerCase()).toContain('restart')
  })

  it('translates the unknown-error fallback instead of hard-coding English', () => {
    const m = eventToMessage({ type: 'tool_result', tool: 'sql', success: false, result: {} })
    expect(m.text).toBe('Unknown error')
  })

  it('renders lifecycle events', () => {
    expect(eventToMessage({ type: 'agent_started' }).text).toContain('Agent started')
    expect(eventToMessage({ type: 'startup_progress', step: 2, total: 7, label: 'Memory' }).text).toContain('[2/7] Memory')
    expect(eventToMessage({ type: 'startup_error', error: 'x' }).type).toBe('error')
    expect(eventToMessage({ type: 'cycle_end', cycle: 4 }).text).toContain('#4')
    expect(eventToMessage({ type: 'report_pushed', report_id: 9 }).text).toContain('9')
    expect(eventToMessage({ type: 'token_truncated', completion_tokens: 10, max_tokens: 10 }).type).toBe('warning')
  })

  it('unwraps activity-log rows ({type, data})', () => {
    const m = eventToMessage({ type: 'chat_reply', created_at: '2026-01-01 10:00:00', data: { type: 'chat_reply', content: 'from log' } })
    expect(m.text).toContain('from log')
  })

  it('returns null for status/usage/agent_waiting frames and logs unknown types without throwing', () => {
    const dbg = vi.spyOn(console, 'debug').mockImplementation(() => {})
    expect(eventToMessage({ type: 'status_update', status: {} })).toBeNull()
    expect(eventToMessage({ type: 'token_usage' })).toBeNull()
    expect(eventToMessage({ type: 'pong' })).toBeNull()
    expect(eventToMessage({ type: 'agent_waiting' })).toBeNull()
    expect(dbg).not.toHaveBeenCalled()
    expect(eventToMessage({ type: 'totally_new_event_type', x: 1 })).toBeNull()
    expect(dbg).toHaveBeenCalledTimes(1)
    // the same unknown type is only logged once
    eventToMessage({ type: 'totally_new_event_type' })
    expect(dbg).toHaveBeenCalledTimes(1)
  })

  it('never throws on malformed events', () => {
    expect(() => eventToMessage({})).not.toThrow()
    expect(() => eventToMessage({ type: 'chat_image' })).not.toThrow()
    expect(() => eventToMessage({ type: 'tool_progress' })).not.toThrow()
    expect(() => eventToMessage({ type: 'chat_tool_result', success: true })).not.toThrow()
  })
})

describe('formatTokenUsage', () => {
  it('formats in/out with optional detail, in English', () => {
    expect(formatTokenUsage(null)).toBeNull()
    expect(formatTokenUsage({})).toBeNull()
    expect(formatTokenUsage({ prompt_tokens: 10, completion_tokens: 5 })).toBe('📊 in 10 / out 5 (response 5)')
    expect(formatTokenUsage({ prompt_tokens: 10, response_tokens: 4, thoughts_tokens: 6, cache_read_tokens: 3, cache_creation_tokens: 2 }))
      .toBe('📊 in 10 / out 4 (thinking 6, response 4, cache hit 3, cache write 2)')
  })
})

describe('de-duplication helpers', () => {
  it('eventKey is stable across WS and activity-log shapes', () => {
    const ws = { type: 'chat_reply', content: 'hello', timestamp: '2026-01-01T10:00:00' }
    const logged = { content: 'hello', timestamp: '2026-01-01T10:00:00', type: 'chat_reply' }
    expect(eventKey('chat_reply', ws)).toBe(eventKey('chat_reply', logged))
    expect(eventKey('chat_reply', ws)).not.toBe(eventKey('chat_reply', { ...ws, content: 'other' }))
    expect(eventKey('chat_reply', ws)).not.toBe(eventKey('chat_reply', { ...ws, timestamp: '2026-01-01T10:00:01' }))
  })

  it('eventTimeMs prefers the event timestamp, then created_at', () => {
    const a = eventTimeMs({ timestamp: '2026-01-01T10:00:00' }, parseBackendDate)
    const b = eventTimeMs({ created_at: '2026-01-01 10:00:00', data: {} }, parseBackendDate)
    expect(a).toBe(Date.parse('2026-01-01T10:00:00Z'))
    expect(b).toBe(a)
    expect(eventTimeMs({ type: 'x' }, parseBackendDate)).toBeNull()
  })

  it('mergeActivity keeps live-only items and drops duplicates of fetched history', () => {
    const item = (id, key, ts) => ({ id, key, ts, message: { text: id } })
    const fetched = [item('h2', 'k2', 200), item('h1', 'k1', 100)]
    const prev = [item('live', 'k3', 300), item('dup', 'k2', 200), item('stream', undefined, 150)]
    const merged = mergeActivity(prev, fetched)
    expect(merged.map((m) => m.id)).toEqual(['live', 'h2', 'stream', 'h1'])
  })

  it('mergeActivity removes only as many duplicates as were fetched, and caps length', () => {
    const item = (id, key, ts) => ({ id, key, ts, message: {} })
    const merged = mergeActivity([item('a', 'k', 2), item('b', 'k', 1)], [item('f', 'k', 2)])
    expect(merged.map((m) => m.id)).toEqual(['f', 'b'])
    const many = Array.from({ length: 10 }, (_, i) => item(`i${i}`, `k${i}`, i))
    expect(mergeActivity(many, [], 3)).toHaveLength(3)
  })
})
