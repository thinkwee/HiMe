/**
 * Pure helpers for the Agent Monitor: turning agent WebSocket / activity-log
 * events into log entries, formatting token usage, and de-duplicating events
 * that arrive twice (activity-log fetch + WebSocket replay backlog).
 *
 * Kept out of the React component so they can be unit-tested.
 */
// Module-level helpers run outside React, so they translate through the
// i18next instance directly instead of the useTranslation() hook.
import i18n from '../i18n'

const TOOL_EVENT_RE = /^(chat|analysis|quick|plan)_tool_(call|result)$/
const VERIFICATION_EVENT_RE = /^(chat|analysis|quick|plan)_verification$/

/** Event types that are handled elsewhere — never logged as "unknown". */
const SILENT_TYPES = new Set(['status_update', 'token_usage', 'pong', 'agent_waiting', 'chat_reply_delta'])

const _warnedTypes = new Set()

const tr = (key, opts) => i18n.t(key, opts)

/** Image endpoint path for a chat_image event (or null when it carries none). */
export function chatImageUrl(d) {
  if (d?.url) return d.url
  if (d?.image_id) return `/api/agent/chat-image/${encodeURIComponent(d.image_id)}`
  return null
}

/**
 * The payload of an event. Activity-log rows arrive as `{created_at, type, data}`
 * with the full original event under `data`; live WS events are the payload
 * themselves — and some of those (tool_progress) carry their own `data` field,
 * so "has a data key" alone cannot tell the two apart.
 */
export function unwrapEvent(ev) {
  if (ev && ev.created_at !== undefined && ev.data && typeof ev.data === 'object') return ev.data
  return ev
}

/**
 * Convert one agent event into a log message object, or null when the event
 * has nothing to display. `ev` is either a raw WS event or an activity-log row
 * (`{type, data, created_at}`).
 */
export function eventToMessage(ev) {
  const d = unwrapEvent(ev)
  const t = ev.type || d.type || ''
  if (SILENT_TYPES.has(t)) return null

  // Determine task type for badge
  const isQuick = d.task === 'quick_analysis' || d.source === 'quick' || t.startsWith('quick_')
  const isPlan = t.startsWith('plan_') || d.source === 'plan'
  const isChat = t === 'user_message' || t.startsWith('chat_')
  const taskType = isQuick ? 'quick' : isPlan ? 'plan' : isChat ? 'chat' : (d.goal ? 'scheduled' : 'analysis')

  let msg = null

  const toolMatch = TOOL_EVENT_RE.exec(t)
  const toolKind = toolMatch ? toolMatch[2] : (t === 'tool_call' ? 'call' : t === 'tool_result' ? 'result' : null)

  if (toolKind === 'call') {
    const toolName = d.tool || ''
    let callText
    if (toolName === 'sql') {
      callText = d.arguments?.query || ''
    } else if (toolName === 'code') {
      callText = d.arguments?.code || d.arguments?.script || ''
    } else if (toolName === 'reply_user') {
      callText = d.arguments?.message || ''
    } else if (toolName === 'update_md') {
      callText = `${d.arguments?.file || '?'}\n${d.arguments?.content || ''}`
    } else if (toolName === 'push_report') {
      callText = d.arguments?.title || (d.arguments?.content || '').slice(0, 120)
    } else {
      callText = JSON.stringify(d.arguments || {}, null, 2)
    }
    msg = { text: callText, type: 'tool_call', toolName }
  } else if (toolKind === 'result') {
    const toolName = d.tool || ''
    if (d.success) {
      const r = d.result || {}
      if (toolName === 'sql' && r.columns && r.rows) {
        const n = r.row_count ?? r.rows.length
        const meta = r.truncated
          ? tr('agent.evt_rows_truncated', { n })
          : tr('agent.evt_rows', { n })
        msg = { text: meta, type: 'tool_result', toolName, toolSuccess: true, sqlData: { columns: r.columns, rows: r.rows } }
      } else {
        let content = r.output ?? r.data ?? r.message ?? ''
        if (typeof content === 'object') content = JSON.stringify(content, null, 2)
        msg = { text: String(content), type: 'tool_result', toolName, toolSuccess: true }
      }
    } else {
      msg = { text: d.result?.error || tr('common.unknown_error'), type: 'tool_result', toolName, toolSuccess: false }
    }
  } else if (VERIFICATION_EVENT_RE.test(t) || t === 'verification_result') {
    msg = {
      text: d.preview || '',
      type: 'verification',
      verifierStatus: d.status || 'verified',
      verifierDetail: d.detail || '',
      verifierEvidenceCount: typeof d.evidence_count === 'number' ? d.evidence_count : 0,
      verifierTool: d.tool || 'reply_user',
    }
  } else {
    switch (t) {
      case 'user_message':
        msg = { text: `${d.sender || tr('agent.evt_user')}: ${d.content}`, type: 'user_input' }
        break
      case 'chat_thinking':
        msg = { text: `💭 ${d.content}`, rawDelta: d.content, type: 'thinking', isStreaming: true }
        break
      case 'chat_content':
        msg = { text: `🤖 ${d.content}`, rawDelta: d.content, type: 'content', isStreaming: true }
        break
      case 'chat_reply':
        // The assistant's final reply to the user (never streamed).
        if (!d.content) break
        msg = { text: `🤖 ${d.content}`, type: 'reply' }
        break
      case 'chat_image':
        msg = {
          text: d.caption || tr('agent.evt_image'),
          type: 'image',
          imageUrl: chatImageUrl(d),
        }
        break
      case 'chat_stopped':
        msg = { text: `⏹ ${tr('agent.evt_chat_stopped')}`, type: 'system' }
        break
      case 'chat_cleared':
        msg = { text: `🧹 ${tr('agent.evt_chat_cleared')}`, type: 'system' }
        break
      case 'tool_progress': {
        let info = d.data
        if (info != null && typeof info !== 'string') {
          try { info = JSON.stringify(info) } catch (_) { info = String(info) }
        }
        msg = {
          text: `⏳ ${tr('agent.evt_progress', { tool: d.tool || '?', info: String(info ?? '').slice(0, 200) })}`,
          type: 'progress',
          progressTool: d.tool || '',
        }
        break
      }
      case 'agent_thinking':
        msg = d.content?.trim() ? { text: `💭 ${d.content}`, rawDelta: d.content, type: 'thinking', isStreaming: true } : null
        break
      case 'content':
        msg = d.content?.trim()
          ? { text: `🤖 ${tr('agent.evt_assistant')}: ${d.content}`, rawDelta: d.content, type: 'content', isStreaming: true }
          : null
        break
      case 'error':
        // A generic error frame: do not claim anything about restarts.
        msg = { text: `❌ ${tr('agent.evt_error', { error: d.error || '' })}`, type: 'error' }
        break
      case 'agent_error':
        msg = { text: `🔄 ${tr('agent.evt_agent_restarted', { error: d.error || tr('agent.evt_unknown') })}`, type: 'warning' }
        break
      case 'agent_started':
        msg = { text: `🚀 ${tr('agent.evt_agent_started')}`, type: 'system' }
        break
      case 'startup_progress':
        msg = { text: `⏳ ${tr('agent.evt_startup_progress', { step: d.step, total: d.total, label: d.label })}`, type: 'system' }
        break
      case 'startup_error':
        msg = { text: `❌ ${tr('agent.evt_startup_failed', { error: d.error || tr('agent.evt_unknown') })}`, type: 'error' }
        break
      case 'agent_stopped':
        msg = { text: `🛑 ${tr('agent.evt_agent_stopped', { reason: d.reason || d.timestamp || '' })}`, type: 'system' }
        break
      case 'cycle_start':
        msg = {
          text: `🔄 ${tr('agent.evt_task_started', { cycle: d.cycle || '' })}${d.goal ? ` — ${d.goal.slice(0, 80)}` : ''}`,
          type: 'system',
        }
        break
      case 'cycle_end':
        msg = { text: `✅ ${tr('agent.evt_task_completed', { cycle: d.cycle || '' })}`, type: 'system' }
        break
      case 'analysis_deferred':
        msg = { text: `⏳ ${tr('agent.evt_analysis_deferred', { age: Math.round(d.data_age_min ?? 0), wait: Math.round(d.max_wait_min ?? 0) })}`, type: 'system' }
        break
      case 'analysis_released':
        msg = { text: `▶️ ${tr(d.reason === 'deadline' ? 'agent.evt_analysis_released_deadline' : 'agent.evt_analysis_released_fresh', { wait: Math.round(d.waited_min ?? 0) })}`, type: 'system' }
        break
      case 'report_pushed':
        msg = { text: `📊 ${tr('agent.evt_report_pushed', { id: d.report_id || '?' })}`, type: 'system' }
        break
      case 'forced_sleep':
        msg = { text: `⚠️ ${tr('agent.evt_forced_sleep', { reason: d.reason || tr('agent.evt_max_turns') })}`, type: 'warning' }
        break
      case 'monitor_connected':
        msg = { text: `📡 ${tr('agent.monitor_connected')}`, type: 'system' }
        break
      case 'token_truncated':
        msg = {
          text: `⚠️ ${tr('agent.evt_token_truncated', { used: d.completion_tokens, max: d.max_tokens })}`,
          type: 'warning',
        }
        break
      case 'quick_analysis_start':
        msg = { text: `🐱 ${tr('agent.evt_quick_start')}`, type: 'system' }
        break
      case 'quick_analysis_complete':
        msg = { text: `🐱 ${tr('agent.evt_quick_complete', { state: d.state || 'neutral' })}`, type: 'system' }
        break
      default:
        if (t && !SILENT_TYPES.has(t) && !_warnedTypes.has(t)) {
          _warnedTypes.add(t)
          // eslint-disable-next-line no-console
          console.debug('[agent-monitor] unhandled event type:', t)
        }
    }
  }

  if (msg) return { ...msg, taskType }
  return null
}

/** Human-readable one-line token usage summary, or null when there is nothing to show. */
export function formatTokenUsage(tu) {
  if (!tu) return null
  const prompt = tu.prompt_tokens
  const completion = tu.completion_tokens ?? tu.response_tokens
  if (prompt == null && completion == null) return null

  const in_ = prompt ?? '-'
  const out = completion ?? '-'
  const parts = []
  if (tu.thoughts_tokens != null && (tu.response_tokens != null || tu.completion_tokens != null)) {
    parts.push(tr('agent.usage_thinking', { n: tu.thoughts_tokens }))
    parts.push(tr('agent.usage_response', { n: tu.response_tokens ?? tu.completion_tokens }))
  } else if (tu.response_tokens != null || tu.completion_tokens != null) {
    parts.push(tr('agent.usage_response', { n: tu.response_tokens ?? tu.completion_tokens }))
  }
  if (tu.cache_read_tokens != null && tu.cache_read_tokens > 0) {
    parts.push(tr('agent.usage_cache_hit', { n: tu.cache_read_tokens }))
  }
  if (tu.cache_creation_tokens != null && tu.cache_creation_tokens > 0) {
    parts.push(tr('agent.usage_cache_write', { n: tu.cache_creation_tokens }))
  }
  const detail = parts.length ? ` (${parts.join(', ')})` : ''
  return `📊 ${tr('agent.usage_line', { in: in_, out })}${detail}`
}

// ---------------------------------------------------------------------------
// De-duplication
// ---------------------------------------------------------------------------

function _hash(str) {
  let h = 5381
  for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) | 0
  return (h >>> 0).toString(36)
}

/**
 * Stable identity for an event: (type, timestamp, content hash). The same
 * event reached through the persisted activity log and the WS replay backlog
 * yields the same key.
 */
export function eventKey(type, d) {
  const data = d || {}
  let body = ''
  try {
    body = JSON.stringify([
      data.content, data.error, data.arguments, data.result, data.message,
      data.tool, data.step, data.caption, data.image_id, data.cycle, data.report_id, data.data,
    ])
  } catch (_) { /* unserialisable: fall back to type + timestamp only */ }
  return `${type}|${data.timestamp || ''}|${_hash(body)}`
}

/**
 * Epoch ms of an activity-log / WS event, or null when it carries no usable time.
 * An activity-log row's `created_at` is monotone with its row id, so it is
 * preferred: the payload `timestamp` is stamped at emit time with a different
 * clock/precision and ordering by it shuffles events within the same second.
 */
export function eventTimeMs(raw, parse) {
  const d = unwrapEvent(raw)
  const stamp = raw?.created_at || d?.timestamp
  if (!stamp) return null
  const ms = parse(stamp).getTime()
  return Number.isNaN(ms) ? null : ms
}

/**
 * Merge freshly fetched (history) log items into the items already on screen
 * without losing live ones and without duplicating anything present in both.
 * Both lists are newest-first; items carry `key` and `ts` (epoch ms).
 */
export function mergeActivity(prev, fetched, max = 500) {
  const fetchedKeys = new Map()
  for (const it of fetched) {
    if (it.key) fetchedKeys.set(it.key, (fetchedKeys.get(it.key) || 0) + 1)
  }
  const prevOnly = []
  for (const it of prev) {
    const n = it.key ? fetchedKeys.get(it.key) || 0 : 0
    if (n > 0) {
      fetchedKeys.set(it.key, n - 1) // already present in the fetched history
    } else {
      prevOnly.push(it)
    }
  }
  const merged = [...fetched, ...prevOnly]
  // Array.prototype.sort is stable: equal timestamps keep their relative order.
  merged.sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))
  return merged.slice(0, max)
}
