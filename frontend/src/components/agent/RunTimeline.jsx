/**
 * Readable agent-activity timeline for the Monitor page.
 *
 * One chronological column (oldest -> newest) of:
 *   - chat runs        user bubble -> steps card -> assistant reply bubble(s)
 *   - background runs  badge header + steps + "Report pushed" link
 *   - notices          agent started / stopped / error, history cleared
 * with a single live-status bubble at the bottom while anything is running.
 *
 * The streaming state (latest thought, reply being typed) lives in an external
 * store (lib/liveStore.js) so a streamed delta re-renders only the live bubble
 * and the streaming reply, never the whole timeline.
 */
import { memo, useCallback, useLayoutEffect, useRef, useState } from 'react'
import { Link, useInRouterContext } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowDown, BookOpen, ChevronRight, ClipboardList, FilePen, FileText, FlaskConical,
  LayoutTemplate, Search, Send, Terminal, Wrench,
} from 'lucide-react'
import { useLive } from '../../lib/liveStore'
import Skeleton from '../Skeleton'
import { ChatImage, CodeBlock, OutputBlock, SqlTable } from './blocks'
import { formatDuration, resultText, stepObject } from '../../pages/runTimeline'

// ---------------------------------------------------------------------------
// Vocabulary: plain verbs per tool (en + zh in the locale files)
// ---------------------------------------------------------------------------

const STEP_META = {
  sql: { icon: Search, labelKey: 'agent.verb_sql' },
  code: { icon: Terminal, labelKey: 'agent.verb_code' },
  read_skill: { icon: BookOpen, labelKey: 'agent.verb_read_skill' },
  analyze: { icon: FlaskConical, labelKey: 'agent.verb_analyze' },
  manage: { icon: ClipboardList, labelKey: 'agent.verb_manage' },
  update_md: { icon: FilePen, labelKey: 'agent.verb_update_md' },
  create_page: { icon: LayoutTemplate, labelKey: 'agent.verb_create_page' },
  push_report: { icon: Send, labelKey: 'agent.verb_push_report' },
}
const GENERIC_META = { icon: Wrench, labelKey: 'agent.verb_generic' }
const MEMORY_SQL_META = { icon: Search, labelKey: 'agent.verb_sql_memory' }

function metaFor(step) {
  if (step.tool === 'sql' && step.args?.database === 'memory') return MEMORY_SQL_META
  return STEP_META[step.tool] || GENERIC_META
}

/** The plain-language sentence for a step, e.g. "Querying your data". */
export function stepVerb(step, t) {
  const meta = metaFor(step)
  return t(meta.labelKey, { tool: step.tool })
}

const STATUS_GLYPH = {
  ok: { glyph: '✓', cls: 'text-ok-ink', labelKey: 'agent.step_ok' },
  running: { glyph: '…', cls: 'text-info-ink animate-pulse', labelKey: 'agent.step_running' },
  error: { glyph: '!', cls: 'text-bad-ink font-bold', labelKey: 'agent.step_error' },
  stopped: { glyph: '■', cls: 'text-ink-3', labelKey: 'agent.step_stopped' },
  interrupted: { glyph: '–', cls: 'text-ink-3', labelKey: 'agent.step_interrupted' },
}

const RUN_STATUS = {
  running: { labelKey: 'agent.run_working', cls: 'text-info-ink' },
  done: { labelKey: 'agent.run_done', cls: 'text-ink-2' },
  stopped: { labelKey: 'agent.run_stopped', cls: 'text-ink-3' },
  error: { labelKey: 'agent.run_failed', cls: 'text-bad-ink' },
  interrupted: { labelKey: 'agent.run_interrupted', cls: 'text-ink-3' },
}

const BG_BADGE = {
  scheduled: { labelKey: 'agent.badge_scheduled', cls: 'bg-warn/15 text-warn-ink' },
  triggered: { labelKey: 'agent.badge_triggered', cls: 'bg-info/15 text-info-ink' },
  plan: { labelKey: 'agent.badge_plan', cls: 'bg-ok/15 text-ok-ink' },
  quick: { labelKey: 'agent.badge_quick', cls: 'bg-bad/15 text-bad-ink' },
  analysis: { labelKey: 'agent.badge_analysis', cls: 'bg-ok/15 text-ok-ink' },
}

const fmtTime = (ts) => {
  try { return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) } catch { return '' }
}
const fmtDay = (ts) => {
  try { return new Date(ts).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) } catch { return '' }
}
const dayKey = (ts) => {
  const d = new Date(ts)
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}
const fmtNum = (n) => (n || 0).toLocaleString()

function countLabel(t, n, singleKey, manyKey) {
  return n === 1 ? t(singleKey) : t(manyKey, { count: n })
}

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

function StepDetail({ step }) {
  const { t } = useTranslation()
  const a = step.args && typeof step.args === 'object' ? step.args : {}
  const r = step.result
  const isTable = r && typeof r === 'object' && Array.isArray(r.columns) && Array.isArray(r.rows)
  const text = isTable ? '' : resultText(r, step.preview)
  let argBlock = null
  if (step.tool === 'sql' && a.query) argBlock = <CodeBlock code={a.query} language="sql" maxH="max-h-32" />
  else if (step.tool === 'code' && (a.code || a.script)) argBlock = <CodeBlock code={a.code || a.script} maxH="max-h-40" />
  else if (['analyze', 'manage'].includes(step.tool)) {
    const goal = a.goal || a.instruction || a.request || a.task
    if (goal) argBlock = <p className="text-xs text-ink-2 whitespace-pre-wrap">{goal}</p>
  } else if (Object.keys(a).length) {
    argBlock = <OutputBlock text={JSON.stringify(a, null, 2)} maxH="max-h-32" />
  }
  return (
    <div className="mt-1.5 mb-1 space-y-1.5">
      {argBlock}
      {isTable && <SqlTable columns={r.columns} rows={r.rows} />}
      {!isTable && text && (
        step.status === 'error'
          ? <OutputBlock text={text} className="!text-bad-ink" />
          : <OutputBlock text={text} />
      )}
      {!argBlock && !isTable && !text && (
        <p className="text-xs text-ink-3">{t('agent.step_no_output')}</p>
      )}
    </div>
  )
}

function StepRow({ step, depth = 0 }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const meta = metaFor(step)
  const Icon = meta.icon
  const glyph = STATUS_GLYPH[step.status] || STATUS_GLYPH.interrupted
  const obj = stepObject(step)
  const dur = step.endTs != null ? formatDuration(step.endTs - step.startTs) : null
  const hasDetail = (step.args && Object.keys(step.args).length > 0) || step.result !== undefined || !!step.preview
  const verb = stepVerb(step, t)
  return (
    <li className="relative pl-7">
      <span className="absolute left-0 top-1 flex h-[19px] w-[19px] items-center justify-center rounded-full border border-line bg-panel text-ink-2">
        <Icon className="h-3 w-3" aria-hidden="true" />
      </span>
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}
        className="group flex w-full items-baseline gap-2 rounded-control py-0.5 text-left text-sm hover:bg-sunken/70 disabled:cursor-default disabled:hover:bg-transparent"
      >
        {hasDetail && (
          <ChevronRight className={`h-3 w-3 shrink-0 self-center text-ink-3 transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true" />
        )}
        <span className={`shrink-0 ${depth ? 'text-ink-2' : 'font-medium text-ink'}`}>{verb}</span>
        {obj && <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2" title={obj}>{obj}</span>}
        {!obj && <span className="flex-1" />}
        {dur && <span className="shrink-0 text-[11px] tabular-nums text-ink-3">{dur}</span>}
        <span
          className={`w-4 shrink-0 text-center text-sm ${glyph.cls}`}
          role="img"
          aria-label={t(glyph.labelKey)}
          title={t(glyph.labelKey)}
        >
          {glyph.glyph}
        </span>
      </button>
      {open && <StepDetail step={step} />}
      {step.children.length > 0 && (
        <ul className="relative mt-0.5 space-y-0.5 before:absolute before:bottom-2 before:left-[9px] before:top-1 before:border-l before:border-dashed before:border-line-2 before:content-['']">
          {step.children.map((c) => <StepRow key={c.id} step={c} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  )
}

function ThoughtRow({ entry }) {
  return (
    <li className="relative pl-7">
      <span className="absolute left-[7px] top-2 h-1.5 w-1.5 rounded-full bg-line-2" aria-hidden="true" />
      <p className="line-clamp-3 whitespace-pre-wrap py-0.5 text-xs italic text-ink-3" title={entry.text}>{entry.text}</p>
    </li>
  )
}

function RunMeta({ run }) {
  const { t } = useTranslation()
  const dur = formatDuration((run.endTs ?? run.lastTs) - run.startTs)
  const tk = run.tokens
  const parts = []
  if (dur && run.status !== 'running') parts.push(dur)
  if (tk && (tk.prompt || tk.response || tk.thoughts)) {
    parts.push(t('agent.run_tokens', { in: fmtNum(tk.prompt), out: fmtNum(tk.response + tk.thoughts) }))
  }
  if (!parts.length) return null
  return <div className="px-3 pb-2 text-[11px] tabular-nums text-ink-3">{parts.join(' · ')}</div>
}

function ReportLink({ id }) {
  const { t } = useTranslation()
  const inRouter = useInRouterContext()
  const label = t('agent.view_reports')
  const cls = 'font-medium text-primary-700 underline-offset-2 hover:underline'
  return inRouter
    ? <Link to="/reports" className={cls}>{label}</Link>
    : <a href="/reports" className={cls} data-report-id={id ?? undefined}>{label}</a>
}

/**
 * The collapsible card of steps. `badge`/`title` turn it into a background-run
 * header; `footer` is true on the last card of a run (totals + report link).
 */
function StepsCard({ run, seg, footer, badge, title }) {
  const { t } = useTranslation()
  const [userOpen, setUserOpen] = useState(null)
  const open = userOpen ?? run.status === 'running'
  const status = RUN_STATUS[run.status] || RUN_STATUS.done
  const stepCount = seg.entries.reduce((n, e) => n + (e.kind === 'step' ? 1 + e.children.length : 0), 0)
  const hiccups = footer ? run.hiccups : seg.entries.reduce(
    (n, e) => n + (e.kind === 'step' ? (e.status === 'error' ? 1 : 0) + e.children.filter((c) => c.status === 'error').length : 0), 0)
  const headline = run.status === 'running' ? t('agent.run_working_named') : t(status.labelKey)
  return (
    <div className="overflow-hidden rounded-card border border-line bg-panel">
      <button
        type="button"
        onClick={() => setUserOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-sunken/60"
      >
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-ink-3 transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true" />
        {badge}
        <span className="min-w-0 flex-1 truncate text-sm">
          {title && <span className="mr-2 text-ink">{title}</span>}
          <span className={`${status.cls} ${run.status === 'running' ? 'font-medium' : ''}`}>{headline}</span>
          <span className="text-ink-3">
            {' · '}{countLabel(t, stepCount, 'agent.run_step_single', 'agent.run_step_many')}
            {hiccups > 0 && <>{' · '}<span className="text-warn-ink">{countLabel(t, hiccups, 'agent.run_hiccup_single', 'agent.run_hiccup_many')}</span></>}
          </span>
        </span>
        {run.runType !== 'chat' && <span className="shrink-0 text-[11px] tabular-nums text-ink-3">{fmtTime(run.startTs)}</span>}
        {run.status === 'running' && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-info" aria-hidden="true" />}
      </button>
      {open && (
        <ul className="relative mx-3 mb-2 mt-0.5 space-y-0.5 before:absolute before:bottom-2 before:left-[9px] before:top-1 before:border-l before:border-dashed before:border-line-2 before:content-['']">
          {seg.entries.map((e) => (e.kind === 'step' ? <StepRow key={e.id} step={e} /> : <ThoughtRow key={e.id} entry={e} />))}
        </ul>
      )}
      {footer && run.warnings.length > 0 && open && (
        <ul className="mx-3 mb-2 space-y-0.5 text-xs text-warn-ink">
          {run.warnings.map((w, i) => <li key={i} className="truncate" title={w.text}>{w.text}</li>)}
        </ul>
      )}
      {footer && <RunMeta run={run} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Bubbles
// ---------------------------------------------------------------------------

function UserBubble({ user }) {
  const { t } = useTranslation()
  const meta = [user.sender, user.channel && user.channel !== 'ios' ? user.channel : ''].filter(Boolean).join(' · ')
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-card rounded-br-chip border border-accent/40 bg-accent/25 px-3.5 py-2 text-sm text-ink">
        {user.content}
      </div>
      <div className="mt-0.5 px-1 text-[11px] text-ink-3">
        {meta || t('agent.evt_user')} · {fmtTime(user.ts)}
      </div>
    </div>
  )
}

function Markdown({ children }) {
  return (
    <div className="prose prose-hime prose-sm max-w-none break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

const ReplyBubble = memo(function ReplyBubble({ seg }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-start">
      <div className="max-w-[92%] rounded-card rounded-bl-chip border border-line bg-panel px-3.5 py-2.5 shadow-sm">
        <Markdown>{seg.content}</Markdown>
        {seg.reportId && (
          <div className="mt-1 text-xs text-ink-3">{t('agent.reply_report', { id: seg.reportId })} <ReportLink id={seg.reportId} /></div>
        )}
      </div>
      <div className="mt-0.5 px-1 text-[11px] text-ink-3">{t('agent.assistant_name')} · {fmtTime(seg.ts)}</div>
    </div>
  )
})

/** The reply being typed (chat_reply_delta). Subscribes to the live store itself. */
function StreamingReply({ store, runKey }) {
  const text = useLive(store, (s) => s.replies[runKey || '_'] || '')
  if (!text) return null
  return (
    <div className="flex flex-col items-start" data-testid="streaming-reply">
      <div className="max-w-[92%] rounded-card rounded-bl-chip border border-line bg-panel px-3.5 py-2.5 shadow-sm">
        <div className="stream-caret">
          <Markdown>{text}</Markdown>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

const ChatRunView = memo(function ChatRunView({ run, store }) {
  const { t } = useTranslation()
  const lastStepsIdx = run.segments.reduce((acc, s, i) => (s.kind === 'steps' ? i : acc), -1)
  return (
    <section className="msg-in space-y-2" aria-label={t('agent.badge_chat')}>
      {run.user && <UserBubble user={run.user} />}
      {run.segments.map((seg, i) => {
        if (seg.kind === 'steps') return <StepsCard key={seg.id} run={run} seg={seg} footer={i === lastStepsIdx} />
        if (seg.kind === 'reply') return <ReplyBubble key={seg.id} seg={seg} />
        return (
          <div key={seg.id} className="max-w-[92%]">
            <ChatImage url={seg.url || (seg.imageId ? `/api/agent/chat-image/${encodeURIComponent(seg.imageId)}` : null)} caption={seg.caption} />
            {seg.caption && <div className="mt-0.5 text-xs text-ink-3">{seg.caption}</div>}
          </div>
        )
      })}
      {run.status === 'running' && <StreamingReply store={store} runKey={run.runId} />}
      {run.status === 'stopped' && (
        <div className="px-1 text-xs italic text-ink-3">{t('agent.reply_stopped')}</div>
      )}
      {run.status === 'error' && (
        <div className="px-1 text-xs text-bad-ink">{t('agent.run_failed')}</div>
      )}
      {lastStepsIdx < 0 && <RunMeta run={run} />}
    </section>
  )
}, (a, b) => a.run.id === b.run.id && a.run.sig === b.run.sig && a.store === b.store)

const BackgroundRunView = memo(function BackgroundRunView({ run }) {
  const { t } = useTranslation()
  const badgeMeta = BG_BADGE[run.runType] || BG_BADGE.analysis
  const badge = <span className={`badge shrink-0 ${badgeMeta.cls}`}>{t(badgeMeta.labelKey)}</span>
  const title = run.goal ? run.goal.replace(/\s+/g, ' ').slice(0, 80) : ''
  const stepsSegs = run.segments.filter((s) => s.kind === 'steps')
  const seg = stepsSegs[0] || { id: `${run.id}:empty`, kind: 'steps', entries: [] }
  return (
    <section className="msg-in space-y-1.5" aria-label={t(badgeMeta.labelKey)}>
      <StepsCard run={run} seg={seg} footer badge={badge} title={title} />
      {run.quickState && (
        <div className="px-1 text-xs text-ink-2">{t('agent.evt_quick_complete', { state: run.quickState })}</div>
      )}
      {run.report && (
        <div className="flex items-center gap-2 rounded-control border border-ok/30 bg-ok/10 px-3 py-1.5 text-sm text-ok-ink">
          <FileText className="h-4 w-4" aria-hidden="true" />
          <span className="font-medium">{t('agent.report_pushed')}</span>
          <ReportLink id={run.report.id} />
        </div>
      )}
    </section>
  )
}, (a, b) => a.run.id === b.run.id && a.run.sig === b.run.sig)

const TONE_CLS = {
  info: 'text-ink-3',
  ok: 'text-ok-ink',
  warn: 'text-warn-ink',
  bad: 'text-bad-ink',
}

function Notice({ item }) {
  const { t } = useTranslation()
  return (
    <div className={`flex items-center justify-center gap-2 py-0.5 text-center text-xs ${TONE_CLS[item.tone] || TONE_CLS.info}`} role={item.tone === 'bad' ? 'alert' : undefined}>
      <span className="min-w-0 truncate">{item.text}</span>
      {item.reportId != null && <ReportLink id={item.reportId} />}
      <span className="shrink-0 text-ink-3">{fmtTime(item.ts)}</span>
      {item.tone === 'bad' && <span className="sr-only">{t('agent.status_error')}</span>}
    </div>
  )
}

function DayDivider({ ts }) {
  return (
    <div className="flex items-center gap-3 py-1 text-[11px] font-medium uppercase tracking-wide text-ink-3" role="separator">
      <span className="h-px flex-1 bg-line" />
      <span>{fmtDay(ts)}</span>
      <span className="h-px flex-1 bg-line" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Live bubble (the ONE live-status area)
// ---------------------------------------------------------------------------

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      {[0, 150, 300].map((d) => (
        <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-3" style={{ animationDelay: `${d}ms` }} />
      ))}
    </span>
  )
}

const THOUGHT_MAX = 200

function LiveBubble({ store, stepText }) {
  const { t } = useTranslation()
  const thought = useLive(store, (s) => s.thought)
  const streaming = useLive(store, (s) => Object.keys(s.replies).length > 0)
  if (streaming) return null
  const clipped = thought.length > THOUGHT_MAX ? `…${thought.slice(-THOUGHT_MAX)}` : thought
  return (
    <div className="msg-in flex flex-col items-start" data-testid="live-bubble">
      <div className="max-w-[92%] rounded-card rounded-bl-chip border border-line bg-sunken px-3.5 py-2.5">
        <div className="flex items-center gap-2 text-sm text-ink">
          <TypingDots />
          <span>{stepText || t('agent.live_thinking')}</span>
        </div>
        {clipped && <p className="mt-1 whitespace-pre-wrap break-words text-xs italic text-ink-3">{clipped}</p>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

export default function RunTimeline({ items, store, liveStepText, isLive, loading, isRunning }) {
  const { t } = useTranslation()
  const scrollRef = useRef(null)
  const innerRef = useRef(null)
  const atBottomRef = useRef(true)
  const [showJump, setShowJump] = useState(false)

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    atBottomRef.current = near
    setShowJump((s) => (s === !near ? s : !near))
  }, [])

  // New content: follow it only when the reader is already at the bottom.
  useLayoutEffect(() => {
    if (atBottomRef.current) scrollToBottom()
  }, [items, isLive, scrollToBottom])

  // Growth that does not come through `items` (live bubble, streaming reply, expanding cards).
  useLayoutEffect(() => {
    const inner = innerRef.current
    if (!inner || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => {
      if (atBottomRef.current) scrollToBottom()
    })
    ro.observe(inner)
    return () => ro.disconnect()
  }, [scrollToBottom])

  const jump = () => {
    atBottomRef.current = true
    setShowJump(false)
    scrollToBottom()
  }

  const rows = []
  let prevDay = null
  for (const it of items) {
    const ts = it.kind === 'run' ? it.startTs : it.ts
    const dk = dayKey(ts)
    if (dk !== prevDay) {
      rows.push(<DayDivider key={`day:${dk}:${it.id}`} ts={ts} />)
      prevDay = dk
    }
    if (it.kind === 'run') {
      rows.push(it.runType === 'chat'
        ? <ChatRunView key={it.id} run={it} store={store} />
        : <BackgroundRunView key={it.id} run={it} />)
    } else {
      rows.push(<Notice key={it.id} item={it} />)
    }
  }

  const empty = !loading && items.length === 0 && !isLive

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto overscroll-contain rounded-card bg-bg px-3 py-4 sm:px-4"
        role="log"
        aria-label={t('agent.agent_activity')}
      >
        <div ref={innerRef} className="space-y-3">
          {loading && <Skeleton label={t('common.loading')} lines={4} card={false} />}
          {empty && (
            <div className="card mx-auto my-8 max-w-md text-center">
              <h4 className="section-title">{t('agent.empty_title')}</h4>
              <p className="mt-1 text-sm text-ink-2">{isRunning ? t('agent.empty_hint_running') : t('agent.empty_hint')}</p>
            </div>
          )}
          {rows}
          {isLive && <LiveBubble store={store} stepText={liveStepText} />}
        </div>
      </div>
      {showJump && (
        <button
          type="button"
          onClick={jump}
          className="btn-secondary absolute bottom-3 left-1/2 -translate-x-1/2 !rounded-full !px-3 !py-1.5 text-xs shadow-lg backdrop-blur"
        >
          <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
          {t('agent.jump_latest')}
        </button>
      )}
    </div>
  )
}
