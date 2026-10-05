/**
 * Pure run-grouping for the Agent Monitor timeline.
 *
 * The monitor keeps a chronological list of *records* (one per interesting
 * agent event, see `toTimelineRecord`). `buildTimeline` folds that list into
 * display items, oldest first:
 *
 *   - a chat run   -> user message, steps (with nested analyze/manage
 *                     sub-steps), assistant replies, charts
 *   - a background -> cron / trigger / plan / quick analysis, grouped by
 *     run             cycle_start..cycle_end (or time-adjacency when the
 *                     cycle markers are missing)
 *   - a notice     -> agent started / stopped / error, history cleared, ...
 *
 * Chat events are grouped by `run_id`; events from before `run_id` existed
 * fall back to time-adjacent grouping. Nothing here touches React or i18n
 * (labels are resolved by the caller), so it is directly unit-testable.
 */

const TOOL_EVENT_RE = /^(chat|analysis|quick|plan)_tool_(call|result)$/

/** Events that belong in the timeline at all (everything else is raw-log only). */
const TIMELINE_TYPES = new Set([
  'user_message', 'chat_reply', 'chat_image', 'chat_stopped', 'chat_cleared',
  'thought', 'draft_held', 'chat_verification', 'token_usage',
  'cycle_start', 'cycle_end', 'report_pushed',
  'quick_analysis_start', 'quick_analysis_complete',
  'agent_started', 'agent_stopped', 'agent_error', 'startup_error', 'agent_waiting',
  'error', 'forced_sleep', 'token_truncated',
  'analysis_deferred', 'analysis_released',
])

/** Tools whose output is the reply bubble itself, so no step row is shown. */
export const HIDDEN_TOOLS = new Set(['reply_user', 'finish_chat'])

const GAP_CHAT_MS = 10 * 60 * 1000
const GAP_BG_MS = 5 * 60 * 1000
const STALE_MS = 15 * 60 * 1000
/** Stragglers (late tool result, the report text) that still belong to a background run that just ended. */
const BG_TAIL_MS = 20 * 1000
/** A held-back draft and the event describing why (verification / stream reset) are the same draft within this window. */
const DRAFT_MERGE_MS = 20 * 1000
/** Verification verdicts that mean the reply was NOT delivered as written. */
const HELD_VERDICTS = new Set(['unverified', 'fabricated', 'rejected', 'blocked'])
/** status_update arrives every ~3s, so "idle" is only trusted after this grace. */
const IDLE_GRACE_MS = 8000

export function isTimelineType(type) {
  return TIMELINE_TYPES.has(type) || TOOL_EVENT_RE.test(type)
}

/**
 * Normalise one event (WS payload or already-unwrapped activity-log payload)
 * into a timeline record, or null when it does not belong in the timeline.
 * `note` is the human text from `eventToMessage` (used for notices/warnings).
 */
export function toTimelineRecord(type, d, { id, key, ts, note } = {}) {
  if (!type || !isTimelineType(type)) return null
  return { id, key, ts: ts ?? 0, type, d: d || {}, note: note || '' }
}

/**
 * Merge freshly fetched (history) records into the records already on screen
 * without losing live ones and without duplicating anything present in both.
 * Both lists are chronological (oldest first); records carry `key` and `ts`.
 */
export function mergeChrono(prev, fetched, max = 1500) {
  const fetchedKeys = new Map()
  for (const r of fetched) {
    if (r.key) fetchedKeys.set(r.key, (fetchedKeys.get(r.key) || 0) + 1)
  }
  const prevOnly = []
  for (const r of prev) {
    const n = r.key ? fetchedKeys.get(r.key) || 0 : 0
    if (n > 0) fetchedKeys.set(r.key, n - 1)
    else prevOnly.push(r)
  }
  const merged = [...fetched, ...prevOnly]
  // Stable: equal timestamps keep their relative order.
  merged.sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0))
  return merged.length > max ? merged.slice(merged.length - max) : merged
}

/**
 * Thread filter for the timeline. 'all' keeps everything; a thread id keeps
 * only that thread's chat runs. Background runs and notices belong to the main
 * thread (every proactive message lands there), so 'main' keeps them too.
 */
export function filterByThread(items, threadId) {
  if (!threadId || threadId === 'all') return items
  return items.filter((it) => {
    if (it.kind === 'run' && it.runType === 'chat') return (it.threadId || 'main') === threadId
    return threadId === 'main'
  })
}

/** Task-type of a background run (matches the badge meaning of the raw log). */
export function backgroundRunType(type, d) {
  if (d.task === 'quick_analysis' || d.source === 'quick' || type.startsWith('quick_')) return 'quick'
  if (type.startsWith('plan_') || d.source === 'plan') return 'plan'
  if (d.source === 'trigger' || d.trigger || d.trigger_rule || d.rule_id) return 'triggered'
  if (d.goal) return 'scheduled'
  return 'analysis'
}

function oneLine(s, max = 90) {
  const flat = String(s ?? '').replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

function firstLine(s, max = 90) {
  const line = String(s ?? '').split('\n').find((l) => l.trim()) || ''
  return oneLine(line, max)
}

/**
 * The monospace "object" of a step (what the tool is working on), per tool.
 * Falls back to the server-provided `summary`.
 */
export function stepObject(step) {
  const a = step.args && typeof step.args === 'object' ? step.args : {}
  let obj = ''
  switch (step.tool) {
    case 'sql': obj = firstLine(a.query); break
    case 'code': obj = firstLine(a.code || a.script); break
    case 'read_skill': obj = a.name || a.skill || a.skill_name || ''; break
    case 'analyze': obj = oneLine(a.goal); break
    case 'manage': obj = oneLine(a.instruction || a.request || a.goal || a.task); break
    case 'update_md': obj = a.file || ''; break
    case 'create_page': obj = a.title || a.page_id || a.name || ''; break
    case 'push_report': obj = a.title || oneLine(a.content, 60); break
    default: break
  }
  if (!obj) obj = oneLine(step.summary)
  return String(obj || '')
}

/** Unwrap a tool result into display text: JSON -> output/content/text/message. */
export function resultText(result, preview) {
  let r = result
  if (typeof r === 'string') {
    const trimmed = r.trim()
    if (trimmed.startsWith('{')) {
      try { r = JSON.parse(trimmed) } catch { /* plain text */ }
    }
  }
  if (r && typeof r === 'object') {
    const inner = r.output ?? r.content ?? r.text ?? r.message ?? r.data
    if (inner !== undefined && inner !== null && inner !== '') {
      return typeof inner === 'string' ? inner : JSON.stringify(inner, null, 2)
    }
    if (r.error) return String(r.error)
    if (Object.keys(r).length === 0 && preview) return String(preview)
    if (r.columns && r.rows) return preview ? String(preview) : ''
    return JSON.stringify(r, null, 2)
  }
  if (r !== undefined && r !== null && r !== '') return String(r)
  return preview ? String(preview) : ''
}

/** "1.2s" / "45s" / "2m 3s"; null when the duration is unknown. */
export function formatDuration(ms) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null
  if (ms < 1000) return `${Math.max(0.1, Math.round(ms / 100) / 10).toFixed(1)}s`
  const s = ms / 1000
  if (s < 10) return `${s.toFixed(1)}s`
  if (s < 60) return `${Math.round(s)}s`
  const m = Math.floor(s / 60)
  return `${m}m ${Math.round(s - m * 60)}s`
}

// ---------------------------------------------------------------------------

export function buildTimeline(records, { now = Date.now(), idle = false } = {}) {
  const items = []
  const chatById = new Map()
  let implicitChat = null
  let openBg = null
  let lastChat = null
  let lastBg = null
  // Ids must not depend on what precedes a run (a history fetch prepends older
  // records), or every run would remount and lose its expanded/collapsed state.
  const idCounts = new Map()
  const uniqueId = (base) => {
    const n = idCounts.get(base) || 0
    idCounts.set(base, n + 1)
    return n ? `${base}#${n}` : base
  }

  const newRun = (init) => {
    const run = {
      kind: 'run',
      id: uniqueId(init.runId ? `run:chat:${init.runId}` : init.cycle != null && init.runType !== 'chat' ? `run:${init.runType}:c${init.cycle}` : `run:${init.runType}:${init.ts}`),
      runType: init.runType,
      runId: init.runId || null,
      threadId: null,
      startTs: init.ts,
      lastTs: init.ts,
      endTs: null,
      status: 'running',
      user: null,
      segments: [],
      stepIndex: [],
      goal: init.goal || '',
      cycle: init.cycle ?? null,
      report: null,
      reply: null,
      settled: false,
      quickState: null,
      tokens: null,
      warnings: [],
      closed: false,
      n: 0,
    }
    items.push(run)
    if (init.runType === 'chat') lastChat = run
    else lastBg = run
    return run
  }

  const touch = (run, ts) => {
    run.n += 1
    if (ts > run.lastTs) run.lastTs = ts
    // More activity after a delivered reply (acknowledgement, then the real answer).
    if (run.settled && !run.closed) {
      run.settled = false
      run.status = 'running'
      run.endTs = null
    }
  }

  const close = (run, status, ts) => {
    if (!run || run.closed) return
    run.closed = true
    run.status = status
    run.endTs = ts
    if (run === openBg) openBg = null
    if (run === implicitChat) implicitChat = null
  }

  const chatRunFor = (rec, create = true) => {
    const run = chatRunForInner(rec, create)
    if (run && rec.d.thread_id && !run.threadId) run.threadId = rec.d.thread_id
    return run
  }

  const chatRunForInner = (rec, create = true) => {
    const rid = rec.d.run_id
    if (rid) {
      let run = chatById.get(rid)
      if (!run && create) {
        run = newRun({ runType: 'chat', runId: rid, ts: rec.ts })
        chatById.set(rid, run)
      }
      return run || null
    }
    if (implicitChat && !implicitChat.closed && rec.ts - implicitChat.lastTs <= GAP_CHAT_MS) return implicitChat
    if (!create) return null
    implicitChat = newRun({ runType: 'chat', ts: rec.ts })
    return implicitChat
  }

  // The background run a straggler event belongs to: the open one, or one that ended moments ago.
  const recentBg = (ts) => {
    if (openBg && !openBg.closed && ts - openBg.lastTs <= GAP_BG_MS) return openBg
    if (lastBg && lastBg.closed && lastBg.runType !== 'quick' && ts - (lastBg.endTs ?? lastBg.lastTs) <= BG_TAIL_MS) return lastBg
    return null
  }

  const bgRunFor = (rec, create = true) => {
    const recent = recentBg(rec.ts)
    if (recent) return recent
    if (!create) return null
    openBg = newRun({
      runType: backgroundRunType(rec.type, rec.d), ts: rec.ts, goal: rec.d.goal, cycle: rec.d.cycle,
    })
    return openBg
  }

  const stepsSegment = (run) => {
    const last = run.segments[run.segments.length - 1]
    if (last && last.kind === 'steps') return last
    const seg = { kind: 'steps', id: `${run.id}:s${run.segments.length}`, entries: [] }
    run.segments.push(seg)
    return seg
  }

  const addStep = (run, step) => {
    run.stepIndex.push(step)
    if (step.parent) {
      for (let i = run.stepIndex.length - 2; i >= 0; i--) {
        const cand = run.stepIndex[i]
        if (!cand.parent && cand.tool === step.parent) {
          cand.children.push(step)
          return
        }
      }
    }
    if (HIDDEN_TOOLS.has(step.tool)) return
    stepsSegment(run).entries.push(step)
  }

  const findStep = (run, d) => {
    const idx = run.stepIndex
    if (d.call_id) {
      for (let i = idx.length - 1; i >= 0; i--) if (idx[i].callId === d.call_id) return idx[i]
    }
    for (let i = idx.length - 1; i >= 0; i--) {
      const s = idx[i]
      if (s.status === 'running' && s.tool === d.tool && (s.parent || null) === (d.parent || null)) return s
    }
    return null
  }

  /** Record (or enrich) a reply the fact check / validator held back. */
  const addDraft = (run, { text, reason, detail, ts }) => {
    const seg = stepsSegment(run)
    for (let i = seg.entries.length - 1; i >= 0; i--) {
      const e = seg.entries[i]
      if (e.kind !== 'draft') continue
      if (ts - e.ts > DRAFT_MERGE_MS) break
      if (text && text.length > e.text.length) e.text = text
      if (reason && (!e.reason || e.reason === 'verification')) e.reason = reason
      if (detail && !e.detail) e.detail = detail
      return
    }
    if (!text && !reason) return
    seg.entries.push({ kind: 'draft', id: `${run.id}:d${seg.entries.length}`, text: text || '', reason: reason || '', detail: detail || '', ts })
  }

  const addNotice = (rec, tone) => {
    items.push({ kind: 'notice', id: uniqueId(`notice:${rec.type}:${rec.ts}`), ts: rec.ts, tone, type: rec.type, text: rec.note || rec.type })
  }

  for (const rec of records) {
    const { type, d, ts } = rec
    const toolMatch = TOOL_EVENT_RE.exec(type)

    if (toolMatch) {
      const isChat = toolMatch[1] === 'chat'
      const run = isChat ? chatRunFor(rec) : bgRunFor(rec)
      touch(run, ts)
      if (toolMatch[2] === 'call') {
        addStep(run, {
          kind: 'step',
          id: `${run.id}:t${run.stepIndex.length}`,
          callId: d.call_id || null,
          tool: d.tool || '',
          parent: d.parent || null,
          args: d.arguments || {},
          summary: d.summary || '',
          status: 'running',
          startTs: ts,
          endTs: null,
          result: undefined,
          preview: '',
          children: [],
        })
      } else {
        let step = findStep(run, d)
        if (!step) {
          // A result with no visible call (history window cut it off): synthesise the step.
          step = {
            kind: 'step', id: `${run.id}:t${run.stepIndex.length}`, callId: d.call_id || null, tool: d.tool || '',
            parent: d.parent || null, args: {}, summary: '', status: 'running', startTs: ts, endTs: null,
            result: undefined, preview: '', children: [],
          }
          addStep(run, step)
        }
        step.status = d.status === 'error' || d.success === false ? 'error' : 'ok'
        step.endTs = ts
        step.result = d.result
        step.preview = d.result_preview || ''
      }
      continue
    }

    switch (type) {
      case 'user_message': {
        // A new message always opens a new run (legacy events carry no run_id).
        let run
        if (d.run_id) {
          run = chatRunFor(rec)
        } else {
          implicitChat = newRun({ runType: 'chat', ts })
          run = implicitChat
        }
        run.user = { content: d.content || '', sender: d.sender || '', channel: d.channel || '', ts }
        touch(run, ts)
        break
      }
      case 'thought': {
        const run = d.scope === 'chat' ? chatRunFor(rec) : bgRunFor(rec)
        stepsSegment(run).entries.push({ kind: 'thought', id: `${run.id}:th${run.n}`, text: d.content || '', ts })
        touch(run, ts)
        break
      }
      case 'chat_reply': {
        if (!d.content) break
        // A report delivered by a scheduled / trigger run is announced as a
        // chat_reply with no run_id: it belongs to that run, not to a chat.
        const bg = d.run_id ? null : recentBg(ts)
        if (bg && !bg.reply && !(implicitChat && !implicitChat.closed && implicitChat.startTs >= bg.startTs)) {
          bg.reply = { content: d.content, hash: d.message_hash || null, reportId: d.report_id || null, ts }
          touch(bg, ts)
          break
        }
        const run = chatRunFor(rec)
        run.segments.push({
          kind: 'reply', id: `${run.id}:r${run.segments.length}`, content: d.content,
          hash: d.message_hash || null, reportId: d.report_id || null, auto: !!d.auto, ts,
        })
        touch(run, ts)
        // A delivered final reply settles the run (a later step re-opens it).
        if (d.final !== false && !run.closed) {
          run.settled = true
          run.status = 'done'
          run.endTs = ts
        }
        break
      }
      case 'chat_verification': {
        // Only a verdict on a chat reply that was not delivered as written is shown.
        if (d.tool && d.tool !== 'reply_user') break
        if (!HELD_VERDICTS.has(d.status)) break
        const run = chatRunFor(rec)
        addDraft(run, { text: d.preview || '', reason: 'verification', detail: d.detail || '', ts })
        touch(run, ts)
        break
      }
      case 'draft_held': {
        const run = chatRunFor(rec)
        addDraft(run, { text: d.content || '', reason: d.reason || '', detail: d.detail || '', ts })
        touch(run, ts)
        break
      }
      case 'chat_image': {
        const run = chatRunFor(rec)
        run.segments.push({
          kind: 'image', id: `${run.id}:i${run.segments.length}`, caption: d.caption || '', url: d.url || null,
          imageId: d.image_id || null, ts,
        })
        touch(run, ts)
        break
      }
      case 'chat_stopped': {
        const run = chatRunFor(rec, false) || lastChat
        if (run) {
          touch(run, ts)
          close(run, 'stopped', ts)
        }
        break
      }
      case 'chat_cleared':
        implicitChat = null
        addNotice(rec, 'info')
        break
      case 'token_usage': {
        const run = d.chat_id ? (chatById.get(d.run_id) || lastChat) : (openBg || lastBg)
        if (!run || run.closed) break
        const t = run.tokens || { prompt: 0, response: 0, thoughts: 0 }
        t.prompt += d.prompt_tokens ?? 0
        t.response += d.response_tokens ?? d.completion_tokens ?? 0
        t.thoughts += d.thoughts_tokens ?? 0
        run.tokens = t
        touch(run, ts)
        break
      }
      case 'cycle_start': {
        if (openBg && !openBg.closed) close(openBg, 'interrupted', ts)
        openBg = newRun({ runType: backgroundRunType(type, d), ts, goal: d.goal, cycle: d.cycle })
        touch(openBg, ts)
        break
      }
      case 'quick_analysis_start': {
        if (openBg && !openBg.closed) close(openBg, 'interrupted', ts)
        openBg = newRun({ runType: 'quick', ts })
        touch(openBg, ts)
        break
      }
      case 'quick_analysis_complete': {
        const run = bgRunFor(rec, false)
        if (run) {
          run.quickState = d.state || null
          touch(run, ts)
          close(run, 'done', ts)
        }
        break
      }
      case 'cycle_end': {
        const run = bgRunFor(rec, false)
        if (run) {
          touch(run, ts)
          close(run, 'done', ts)
        }
        break
      }
      case 'report_pushed': {
        const run = openBg || (lastBg && ts - lastBg.lastTs <= 2 * 60 * 1000 ? lastBg : null)
        if (run) {
          run.report = { id: d.report_id ?? null, ts }
          touch(run, ts)
        } else {
          items.push({ kind: 'notice', id: uniqueId(`notice:report:${ts}`), ts, tone: 'ok', type, text: rec.note, reportId: d.report_id ?? null })
        }
        break
      }
      case 'agent_stopped':
        for (const it of items) if (it.kind === 'run' && !it.closed) close(it, 'stopped', ts)
        addNotice(rec, 'info')
        break
      case 'agent_error':
        for (const it of items) if (it.kind === 'run' && !it.closed) close(it, 'error', ts)
        addNotice(rec, 'bad')
        break
      case 'agent_started':
      case 'agent_waiting':
      case 'analysis_deferred':
      case 'analysis_released':
        addNotice(rec, 'info')
        break
      case 'startup_error':
        addNotice(rec, 'bad')
        break
      case 'error':
      case 'forced_sleep':
      case 'token_truncated': {
        const run = d.chat_id ? (chatById.get(d.run_id) || lastChat) : openBg
        if (run && !run.closed) {
          run.warnings.push({ type, text: rec.note, ts })
          touch(run, ts)
        } else {
          addNotice(rec, type === 'error' ? 'bad' : 'warn')
        }
        break
      }
      default:
        break
    }
  }

  // Finalise: open runs become done (agent idle) or interrupted (stale), and
  // steps that never got a result inherit the run's fate.
  for (const it of items) {
    if (it.kind !== 'run') continue
    if (it.status === 'running') {
      if (idle && now - it.lastTs > IDLE_GRACE_MS) it.status = 'done'
      else if (now - it.lastTs > STALE_MS) it.status = 'interrupted'
    }
    if (it.status !== 'running' && !it.endTs) it.endTs = it.lastTs
    const dangling = it.status === 'stopped' ? 'stopped' : 'interrupted'
    let errors = 0
    let visible = 0
    for (const s of it.stepIndex) {
      if (s.status === 'running' && it.status !== 'running') {
        s.status = it.status === 'done' ? 'interrupted' : dangling
      }
      if (s.status === 'error') errors += 1
      if (!HIDDEN_TOOLS.has(s.tool)) visible += 1
    }
    // A draft that was in the end delivered unchanged is not "held back".
    const delivered = new Set(it.segments.filter((x) => x.kind === 'reply').map((x) => x.content.replace(/\s+/g, ' ').trim()))
    for (const seg of it.segments) {
      if (seg.kind !== 'steps') continue
      seg.entries = seg.entries.filter((e) => e.kind !== 'draft' || !e.text || !delivered.has(e.text.replace(/\s+/g, ' ').trim()))
    }
    it.segments = it.segments.filter((x) => x.kind !== 'steps' || x.entries.length > 0)
    it.stepCount = visible
    it.hiccups = errors + it.warnings.length
    it.sig = `${it.n}:${it.status}:${it.stepIndex.length}:${it.threadId || ''}`
  }

  return items
}

/** Latest run still in progress, or null. */
export function findLiveRun(items) {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'run' && it.status === 'running') return it
  }
  return null
}

/** The step currently executing in a run (deepest running child first), or null. */
export function currentStep(run) {
  if (!run) return null
  for (let i = run.stepIndex.length - 1; i >= 0; i--) {
    const s = run.stepIndex[i]
    if (s.status === 'running' && !HIDDEN_TOOLS.has(s.tool)) return s
  }
  return null
}
