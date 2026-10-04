import { memo, useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { formatFullDateTime, parseBackendDate } from '../lib/utils'
import { useAppActions } from '../context/AppContext'
import InlineFlash from '../components/InlineFlash'
import { createLiveStore } from '../lib/liveStore'
import RunTimeline, { stepVerb } from '../components/agent/RunTimeline'
import { ChatImage, CodeBlock } from '../components/agent/blocks'
import { useDocumentVisible, useFlash, useOnActivate, usePolling } from '../lib/hooks'
import {
  eventKey, eventTimeMs, eventToMessage, formatTokenUsage, mergeActivity, unwrapEvent,
} from './agentEvents'
import {
  buildTimeline, currentStep, findLiveRun, mergeChrono, stepObject, toTimelineRecord,
} from './runTimeline'
import { Play, Square, Brain, Activity, Database, Wifi, WifiOff, X, Clock, Plus, Pause, Trash2, Zap, RotateCcw, Pencil, Check, Loader2, CheckCircle2, AlertCircle, Server, HardDrive, Cpu, ListChecks, Rocket } from 'lucide-react'

const STORAGE_KEY = 'hime_agent_config'

function loadStoredConfig() {
  try {
    const s = localStorage.getItem(STORAGE_KEY)
    if (s) {
      const c = JSON.parse(s)
      return {
        llmProvider: c.llmProvider || 'gemini',
        model: c.model || '',
      }
    }
  } catch (e) {
    console.warn('Failed to load stored config:', e)
  }
  return null
}

function saveConfig(config) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch (e) {
    console.warn('Failed to save config:', e)
  }
}

// formatFullDateTime imported from ../lib/utils

/** Unique log id. crypto.randomUUID() only exists in secure contexts, and the
 *  dashboard is routinely served over plain http:// on the LAN. */
const genId = () =>
  (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`

const TASK_TYPE_BADGE = {
  analysis: { labelKey: 'agent.badge_analysis', cls: 'bg-ok/15 text-ok-ink' },
  chat: { labelKey: 'agent.badge_chat', cls: 'bg-info/15 text-info-ink' },
  scheduled: { labelKey: 'agent.badge_scheduled', cls: 'bg-warn/15 text-warn-ink' },
  quick: { labelKey: 'agent.badge_quick', cls: 'bg-bad/15 text-bad-ink' },
  plan: { labelKey: 'agent.badge_plan', cls: 'bg-ok/15 text-ok-ink' },
}

// Per-tool theme colours — avoids clashing with task-type badge colours
const TOOL_THEME = {
  sql:            { bg: 'bg-info/10',   text: 'text-info-ink',    header: 'bg-info/15 text-info-ink',    border: 'border-info/30',    labelKey: 'agent.tool_sql',         icon: '🔍' },
  code:           { bg: 'bg-info/10',  text: 'text-info-ink',  header: 'bg-info/15 text-info-ink', border: 'border-info/30', labelKey: 'agent.tool_code',        icon: '⚡' },
  push_report:    { bg: 'bg-info/10',    text: 'text-info-ink',    header: 'bg-info/15 text-info-ink',    border: 'border-info/30',    labelKey: 'agent.tool_push_report', icon: '📊' },
  update_md:      { bg: 'bg-sunken/60',   text: 'text-ink-2',   header: 'bg-sunken/60 text-ink',  border: 'border-line/50',   labelKey: 'agent.tool_update_md',   icon: '📝' },
  reply_user:     { bg: 'bg-info/10',     text: 'text-info-ink',     header: 'bg-info/15 text-info-ink',      border: 'border-info/30',     labelKey: 'agent.tool_reply_user',  icon: '✉️' },
  finish_chat:    { bg: 'bg-sunken/60',   text: 'text-ink-2',   header: 'bg-sunken/60 text-ink',  border: 'border-line/50',   labelKey: 'agent.tool_finish_chat', icon: '💬' },
  sleep:          { bg: 'bg-sunken/60',   text: 'text-ink-2',   header: 'bg-sunken/60 text-ink',  border: 'border-line/50',   labelKey: 'agent.tool_sleep',       icon: '💤' },
  create_page:    { bg: 'bg-bad/10',    text: 'text-bad-ink',    header: 'bg-bad/15 text-bad-ink',    border: 'border-bad/30',    labelKey: 'agent.tool_create_page', icon: '🧩' },
  read_skill:     { bg: 'bg-warn/10',   text: 'text-warn-ink',   header: 'bg-warn/15 text-warn-ink',  border: 'border-warn/30',   labelKey: 'agent.tool_read_skill',  icon: '📖' },
  analyze:        { bg: 'bg-ok/10', text: 'text-ok-ink', header: 'bg-ok/15 text-ok-ink', border: 'border-ok/30', labelKey: 'agent.tool_analyze',   icon: '🔬' },
  manage:         { bg: 'bg-warn/10',  text: 'text-warn-ink',  header: 'bg-warn/15 text-warn-ink', border: 'border-warn/30', labelKey: 'agent.tool_manage',     icon: '🗂️' },
}
const DEFAULT_TOOL_THEME = { bg: 'bg-sunken/60', text: 'text-ink-2', header: 'bg-sunken/60 text-ink', border: 'border-line/50', labelKey: 'agent.tool_generic', icon: '🔧' }

function ToolResultBlock({ text, sqlData, theme }) {
  // SQL table
  if (sqlData) {
    return (
      <div className="mt-1 overflow-x-auto max-h-80 overflow-y-auto">
        <table className="text-[10px] border-collapse w-full">
          <thead>
            <tr className={theme.header}>
              {sqlData.columns.map((col, i) => (
                <th key={i} className={`px-2 py-0.5 text-left font-semibold border ${theme.border}`}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sqlData.rows.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 0 ? 'bg-panel/50' : theme.bg}>
                {row.map((val, ci) => (
                  <td key={ci} className={`px-2 py-0.5 border ${theme.border} text-ink max-w-xs`}>
                    {val == null ? <span className="text-ink-3">null</span> : String(val).length > 200 ? String(val).slice(0, 200) + '…' : String(val)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  // Short inline result — no block needed
  if (!text || (text.length < 80 && !text.includes('\n'))) return null
  // Code tool output — show as plain monospace (output, not source code)
  return (
    <div className={`mt-1 ${theme.bg} rounded-chip px-2.5 py-1.5 border ${theme.border} font-mono text-[10px] ${theme.text} whitespace-pre-wrap max-h-40 overflow-y-auto`}>
      {text}
    </div>
  )
}

// Syntax-highlighted Python code block (raw log)
function PythonBlock({ code, maxH = 'max-h-32' }) {
  return <CodeBlock code={code} className="mt-1" maxH={maxH} fontSize="10px" />
}

const LogItem = memo(function LogItem({ update }) {
  const { t } = useTranslation()
  const msg = update.message
  const isObj = typeof msg === 'object' && msg !== null
  const text = isObj ? msg.text : msg
  const type = isObj ? msg.type : 'default'
  const taskType = isObj ? msg.taskType : 'analysis'
  const tokenUsage = isObj ? msg.tokenUsage : null
  const toolName = isObj ? msg.toolName : null
  const toolSuccess = isObj ? msg.toolSuccess : true
  const sqlData = isObj ? msg.sqlData : null
  const badge = TASK_TYPE_BADGE[taskType] || TASK_TYPE_BADGE.analysis
  const theme = toolName ? (TOOL_THEME[toolName] || DEFAULT_TOOL_THEME) : null

  // ── Fact-verifier verdict ──────────────────────────────────────────────
  if (type === 'verification') {
    const status = isObj ? msg.verifierStatus : 'verified'
    const detail = isObj ? msg.verifierDetail : ''
    const evidenceCount = isObj ? msg.verifierEvidenceCount : 0
    const verifierTool = isObj ? msg.verifierTool : 'reply_user'
    const VERIFIER_THEME = {
      verified:           { icon: '🛡️✅', cls: 'bg-ok/15 text-ok-ink border-ok/30', textCls: 'text-ok-ink' },
      fabricated:         { icon: '🛡️🚫', cls: 'bg-bad/15 text-bad-ink border-bad/30',             textCls: 'text-bad-ink font-semibold' },
      unverified:         { icon: '🛡️⚠️', cls: 'bg-warn/15 text-warn-ink border-warn/30',       textCls: 'text-warn-ink font-medium' },
      no_evidence_needed: { icon: '🛡️·',  cls: 'bg-sunken/70 text-ink border-line',         textCls: 'text-ink-2' },
    }
    const vt = VERIFIER_THEME[status] || VERIFIER_THEME.verified
    const statusLabelKey = `agent.verifier_status_${status}`
    return (
      <div className="mb-1.5 pb-1 border-b border-line/50 last:border-0">
        <span className="text-ink-3 select-none mr-1.5">[{update.time}]</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded-chip mr-1.5 ${badge.cls}`}>{t(badge.labelKey)}</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0.5 rounded-chip mr-1 border ${vt.cls}`}>
          {vt.icon} {t('agent.verifier_label')}
        </span>
        <span className={`text-[10px] ${vt.textCls}`}>
          {t(statusLabelKey, t('agent.verifier_status_verified'))}
          {evidenceCount > 0 && (
            <span className="ml-1.5 text-ink-2 font-normal">
              · {t('agent.verifier_evidence_count', { count: evidenceCount })}
            </span>
          )}
          {verifierTool && verifierTool !== 'reply_user' && (
            <span className="ml-1.5 text-ink-3 font-normal">· {verifierTool}</span>
          )}
        </span>
        {text && (
          <div className="mt-1 text-[10px] text-ink-2 pl-4 truncate" title={text}>
            &quot;{text}&quot;
          </div>
        )}
        {detail && (
          <div className="mt-1 text-[10px] text-ink-2 italic pl-4">{detail}</div>
        )}
      </div>
    )
  }

  // ── Tool call ──────────────────────────────────────────────────────────
  if (type === 'tool_call' && theme) {
    return (
      <div className="mb-1.5 pb-1 border-b border-line/50 last:border-0">
        <span className="text-ink-3 select-none mr-1.5">[{update.time}]</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded-chip mr-1.5 ${badge.cls}`}>{t(badge.labelKey)}</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0.5 rounded-chip mr-1 ${theme.header}`}>{theme.icon} {t(theme.labelKey)}</span>
        {/* Short args inline, long args in a block; code tool gets syntax highlighting */}
        {text && text.length < 100 && !text.includes('\n') ? (
          <span className={`${theme.text} font-mono text-[10px]`}> {text}</span>
        ) : text && toolName === 'code' ? (
          <PythonBlock code={text} theme={theme} />
        ) : text ? (
          <div className={`mt-1 ${theme.bg} rounded-chip px-2.5 py-1.5 border ${theme.border} font-mono text-[10px] ${theme.text} whitespace-pre-wrap max-h-32 overflow-y-auto`}>
            {text}
          </div>
        ) : null}
      </div>
    )
  }

  // ── Tool result ────────────────────────────────────────────────────────
  if (type === 'tool_result' && theme) {
    const icon = toolSuccess ? '✅' : '❌'
    // Will a detail block be rendered below? If so, don't repeat text inline.
    const hasBlock = toolSuccess && (sqlData || (text && text.length >= 80 && text.includes('\n')))
    return (
      <div className="mb-1.5 pb-1 border-b border-line/50 last:border-0">
        <span className="text-ink-3 select-none mr-1.5">[{update.time}]</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded-chip mr-1.5 ${badge.cls}`}>{t(badge.labelKey)}</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0.5 rounded-chip mr-1 ${theme.header}`}>{theme.icon} {t(theme.labelKey)}</span>
        <span className={toolSuccess ? theme.text : 'text-bad-ink font-medium'}>
          {icon}{hasBlock ? '' : ` ${text}`}
        </span>
        {toolSuccess && <ToolResultBlock text={text} toolName={toolName} sqlData={sqlData} theme={theme} />}
      </div>
    )
  }

  // ── Non-tool event types ───────────────────────────────────────────────
  let textColor = 'text-ink'
  let bgColor = ''

  if (type === 'thinking') {
    textColor = 'text-info-ink italic'
  } else if (type === 'content') {
    textColor = 'text-info-ink font-medium'
    bgColor = 'bg-info/10 rounded-chip px-1'
  } else if (type === 'reply' || type === 'image') {
    textColor = 'text-info-ink font-medium'
    bgColor = 'bg-info/10 rounded-chip px-1'
  } else if (type === 'progress') {
    textColor = 'text-ink-3 text-[10px]'
  } else if (type === 'user_input') {
    textColor = 'text-warn-ink font-bold'
    bgColor = 'bg-warn/10 rounded-chip px-1'
  } else if (type === 'error') {
    textColor = 'text-bad-ink font-medium'
  } else if (type === 'warning') {
    textColor = 'text-warn-ink font-medium'
  } else if (type === 'system') {
    textColor = 'text-ink-2 font-medium'
  } else if (type === 'token_usage') {
    textColor = 'text-ink-2 font-mono text-xs'
  }

  const tokenLine = (type === 'content' || type === 'token_usage') && tokenUsage ? formatTokenUsage(tokenUsage) : (type === 'token_usage' ? text : null)

  return (
    <div className={`mb-1.5 pb-1 border-b border-line/50 last:border-0 whitespace-pre-wrap ${bgColor}`}>
      <span className="text-ink-3 select-none mr-1.5">[{update.time}]</span>
      {type !== 'system' && type !== 'token_usage' && (
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded-chip mr-1.5 ${badge.cls}`}>
          {t(badge.labelKey)}
        </span>
      )}
      <span className={textColor}>{type === 'token_usage' ? (tokenLine || text) : text}</span>
      {type !== 'token_usage' && tokenLine && (
        <div className="mt-1 text-xs text-ink-2 font-mono">{tokenLine}</div>
      )}
      {type === 'image' && isObj && msg.imageUrl && <ChatImage url={msg.imageUrl} caption={text} />}
    </div>
  )
})

// ---------------------------------------------------------------------------
// Scheduled Tasks Panel
// ---------------------------------------------------------------------------
function ScheduledTasksPanel({ isRunning, active }) {
  const { t } = useTranslation()
  const [flash, setFlash] = useFlash()
  const [loadError, setLoadError] = useState('')
  const [tasks, setTasks] = useState([])
  const [serverTz, setServerTz] = useState('UTC')
  const [showAdd, setShowAdd] = useState(false)
  const [newCron, setNewCron] = useState('0 8 * * *')
  const [newGoal, setNewGoal] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editCron, setEditCron] = useState('')
  const [editGoal, setEditGoal] = useState('')

  const fetchTasks = useCallback(async () => {
    const res = await api.getScheduledTasks()
    if (res.success) {
      setTasks(res.tasks || [])
      if (res.timezone) setServerTz(res.timezone)
      setLoadError('')
    } else {
      setLoadError(res.error || t('common.load_failed'))
    }
  }, [t])

  useEffect(() => {
    // set-state-in-effect is a false positive here: fetchTasks is async and every
    // setState inside it runs after `await api.getScheduledTasks()`, i.e. in a
    // later microtask, never synchronously during this effect body. The rule
    // cannot see through the await when the callee is a memoized function.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchTasks()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // Poll only while this page is the active route and the tab is visible.
  usePolling(fetchTasks, 15000, active)
  useOnActivate(active, fetchTasks)

  const handleCreate = async () => {
    if (!newCron.trim() || !newGoal.trim()) return
    const res = await api.createScheduledTask(newCron.trim(), newGoal.trim())
    if (res.success) {
      setNewCron('0 8 * * *')
      setNewGoal('')
      setShowAdd(false)
      fetchTasks()
    } else {
      setFlash('error', res.error || t('agent.failed_create_task'))
    }
  }

  const handleToggle = async (task) => {
    const newStatus = task.status === 'active' ? 'paused' : 'active'
    const res = await api.updateScheduledTask(task.id, { status: newStatus })
    if (!res.success) setFlash('error', res.error || t('common.unknown_error'))
    fetchTasks()
  }

  const handleDelete = async (task) => {
    if (!window.confirm(t('agent.confirm_delete_task'))) return
    const res = await api.updateScheduledTask(task.id, { status: 'deleted' })
    if (!res.success) setFlash('error', res.error || t('common.unknown_error'))
    fetchTasks()
  }

  const handleTrigger = async (task) => {
    if (!isRunning) { setFlash('error', t('agent.start_agent_first')); return }
    const res = await api.triggerAnalysis(task.prompt_goal)
    if (res.success) setFlash('success', t('agent.analysis_queued'))
    else setFlash('error', res.error || t('common.unknown_error'))
  }

  const handleEdit = (task) => {
    setEditingId(task.id)
    setEditCron(task.cron_expr)
    setEditGoal(task.prompt_goal)
  }

  const handleSaveEdit = async () => {
    if (!editCron.trim() || !editGoal.trim()) return
    const res = await api.updateScheduledTask(editingId, { cron_expr: editCron.trim(), prompt_goal: editGoal.trim() })
    if (!res.success) { setFlash('error', res.error || t('common.unknown_error')); return }
    setEditingId(null)
    fetchTasks()
  }

  const cronHuman = (expr) => {
    const parts = expr.split(' ')
    if (parts.length !== 5) return expr
    const [min, hour, dom, month, dow] = parts
    // Only plain "every day / every weekday at HH:MM" expressions can be
    // summarised; anything with ranges, steps, lists or a day/month
    // restriction is shown verbatim so it isn't mis-described.
    if (dom !== '*' || month !== '*') return expr
    if ([min, hour, dow].some((f) => /[*/,-]/.test(f) && f !== '*')) return expr
    if (min === '*' || hour === '*') return expr
    const dowMap = {
      '0': t('agent.weekday_0'), '1': t('agent.weekday_1'), '2': t('agent.weekday_2'), '3': t('agent.weekday_3'),
      '4': t('agent.weekday_4'), '5': t('agent.weekday_5'), '6': t('agent.weekday_6'), '7': t('agent.weekday_0'),
      '*': t('agent.cron_daily'),
    }
    const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
    return `${dowMap[dow] || dow} ${time}`
  }

  return (
    <div className="card !p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="section-title flex items-center gap-2">
          <Clock className="w-5 h-5 text-ink-2" />
          {t('agent.scheduled_tasks')}
        </h3>
        <button
          type="button"
          onClick={() => setShowAdd(!showAdd)}
          aria-expanded={showAdd}
          className="text-xs flex items-center gap-1 text-primary-600 hover:text-primary-800"
        >
          <Plus className="w-3 h-3" /> {t('common.add')}
        </button>
      </div>

      <p className="-mt-2 mb-3 text-[11px] text-ink-2">
        {t('agent.cron_timezone_hint', { tz: serverTz })}
      </p>
      <InlineFlash flash={flash} className="mb-3" />
      {loadError && (
        <div role="alert" className="mb-3 flex items-center gap-2 text-xs text-bad-ink">
          <span className="flex-1">{loadError}</span>
          <button type="button" onClick={fetchTasks} className="underline">{t('common.retry')}</button>
        </div>
      )}

      {showAdd && (
        <div className="mb-3 p-3 bg-sunken rounded-chip border border-line space-y-2">
          <input
            type="text"
            value={newCron}
            onChange={(e) => setNewCron(e.target.value)}
            placeholder={t('agent.cron_placeholder')}
            aria-label={t('agent.cron_placeholder')}
            className="input w-full text-sm font-mono"
          />
          <textarea
            value={newGoal}
            onChange={(e) => setNewGoal(e.target.value)}
            placeholder={t('agent.analysis_goal_placeholder')}
            aria-label={t('agent.analysis_goal_placeholder')}
            className="input w-full text-sm"
            rows={2}
          />
          <div className="flex gap-2">
            <button onClick={handleCreate} className="btn btn-primary text-xs px-3 py-1">{t('common.create')}</button>
            <button onClick={() => setShowAdd(false)} className="btn text-xs px-3 py-1">{t('common.cancel')}</button>
          </div>
        </div>
      )}

      {tasks.length === 0 ? (
        <p className="text-sm text-ink-3 text-center py-4">{t('agent.no_scheduled_tasks')}</p>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            editingId === task.id ? (
              <div key={task.id} className="p-2.5 rounded-chip border border-primary-200 bg-primary-50/30 text-sm space-y-2">
                <input type="text" value={editCron} onChange={(e) => setEditCron(e.target.value)} className="input w-full text-sm font-mono" placeholder={t('agent.cron_expression')} aria-label={t('agent.cron_expression')} />
                <textarea value={editGoal} onChange={(e) => setEditGoal(e.target.value)} className="input w-full text-sm" rows={2} placeholder={t('agent.analysis_goal_placeholder')} aria-label={t('agent.analysis_goal_placeholder')} />
                <div className="flex gap-1">
                  <button onClick={handleSaveEdit} className="p-1 hover:bg-ok/10 rounded-chip" title={t('common.save')} aria-label={t('common.save')}><Check className="w-3.5 h-3.5 text-ok-ink" /></button>
                  <button onClick={() => setEditingId(null)} className="p-1 hover:bg-sunken rounded-chip" title={t('common.cancel')} aria-label={t('common.cancel')}><X className="w-3.5 h-3.5 text-ink-3" /></button>
                </div>
              </div>
            ) : (
              <div key={task.id} className={`p-2.5 rounded-chip border text-sm ${task.status === 'active' ? 'bg-panel border-line' : 'bg-sunken border-line opacity-60'}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-xs bg-sunken px-1.5 py-0.5 rounded-chip">{cronHuman(task.cron_expr)}</span>
                      <span className={`text-[10px] font-bold uppercase px-1.5 rounded-chip ${task.status === 'active' ? 'bg-ok/15 text-ok-ink' : 'bg-warn/15 text-warn-ink'}`}>
                        {t(`agent.status_${task.status}`, task.status)}
                      </span>
                    </div>
                    <p className="text-ink text-xs truncate">{task.prompt_goal}</p>
                  </div>
                  <div className="flex gap-1 flex-shrink-0">
                    {isRunning && (
                      <button onClick={() => handleTrigger(task)} className="p-1 hover:bg-info/10 rounded-chip" title={t('agent.run_now')} aria-label={t('agent.run_now')}>
                        <Play className="w-3.5 h-3.5 text-info" />
                      </button>
                    )}
                    <button onClick={() => handleEdit(task)} className="p-1 hover:bg-info/10 rounded-chip" title={t('common.edit')} aria-label={t('common.edit')}>
                      <Pencil className="w-3.5 h-3.5 text-info" />
                    </button>
                    <button onClick={() => handleToggle(task)} className="p-1 hover:bg-warn/10 rounded-chip" title={task.status === 'active' ? t('agent.pause') : t('agent.resume')} aria-label={task.status === 'active' ? t('agent.pause') : t('agent.resume')}>
                      {task.status === 'active' ? <Pause className="w-3.5 h-3.5 text-warn" /> : <RotateCcw className="w-3.5 h-3.5 text-ok" />}
                    </button>
                    <button onClick={() => handleDelete(task)} className="p-1 hover:bg-bad/10 rounded-chip" title={t('common.delete')} aria-label={t('common.delete')}>
                      <Trash2 className="w-3.5 h-3.5 text-bad" />
                    </button>
                  </div>
                </div>
              </div>
            )
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Trigger Rules Panel
// ---------------------------------------------------------------------------
const TRIGGER_CONDITIONS = ['gt', 'lt', 'gte', 'lte', 'avg_gt', 'avg_lt', 'spike', 'drop', 'delta_gt', 'absent']

// Compact symbols for the rule summary line; words come from i18n.
const CONDITION_SYMBOLS = { gt: '>', lt: '<', gte: '\u2265', lte: '\u2264', avg_gt: 'avg >', avg_lt: 'avg <', delta_gt: '\u0394 >' }

function ConditionSelect({ value, onChange, className = '' }) {
  const { t } = useTranslation()
  return (
    <select value={value} onChange={onChange} aria-label={t('agent.condition')} className={`input text-sm ${className}`}>
      {TRIGGER_CONDITIONS.map((v) => <option key={v} value={v}>{t(`agent.cond_${v}`)}</option>)}
    </select>
  )
}

function TriggerRulesPanel({ isRunning, active }) {
  const { t } = useTranslation()
  const [flash, setFlash] = useFlash()
  const [loadError, setLoadError] = useState('')
  const [rules, setRules] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [editingId, setEditingId] = useState(null)

  const emptyRule = { name: '', feature_type: '', condition: 'gt', threshold: '', window_minutes: 60, cooldown_minutes: 30, prompt_goal: '' }
  const [newRule, setNewRule] = useState(emptyRule)
  const [editRule, setEditRule] = useState({})

  const fetchRules = useCallback(async () => {
    const res = await api.getTriggerRules()
    if (res.success) {
      setRules(res.rules || [])
      setLoadError('')
    } else {
      setLoadError(res.error || t('common.load_failed'))
    }
  }, [t])

  useEffect(() => {
    // False positive — see the note on the scheduled-tasks poll above: fetchRules
    // only setStates after `await api.getTriggerRules()`, never synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchRules()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  usePolling(fetchRules, 15000, active)
  useOnActivate(active, fetchRules)

  const handleCreate = async () => {
    if (!newRule.name.trim() || !newRule.feature_type.trim() || !newRule.prompt_goal.trim()) return
    const res = await api.createTriggerRule({
      ...newRule,
      threshold: parseFloat(newRule.threshold) || 0,
      window_minutes: parseInt(newRule.window_minutes) || 60,
      cooldown_minutes: parseInt(newRule.cooldown_minutes) || 30,
    })
    if (res.success) {
      setNewRule(emptyRule)
      setShowAdd(false)
      fetchRules()
    } else {
      setFlash('error', res.error || t('agent.failed_create_rule'))
    }
  }

  const handleToggle = async (rule) => {
    const newStatus = rule.status === 'active' ? 'paused' : 'active'
    const res = await api.updateTriggerRule(rule.id, { status: newStatus })
    if (!res.success) setFlash('error', res.error || t('common.unknown_error'))
    fetchRules()
  }

  const handleDelete = async (rule) => {
    if (!window.confirm(t('agent.confirm_delete_rule'))) return
    const res = await api.updateTriggerRule(rule.id, { status: 'deleted' })
    if (!res.success) setFlash('error', res.error || t('common.unknown_error'))
    fetchRules()
  }

  const handleTrigger = async (rule) => {
    if (!isRunning) { setFlash('error', t('agent.start_agent_first')); return }
    const res = await api.triggerAnalysis(rule.prompt_goal)
    if (res.success) setFlash('success', t('agent.analysis_queued'))
    else setFlash('error', res.error || t('common.unknown_error'))
  }

  const handleEdit = (rule) => {
    setEditingId(rule.id)
    setEditRule({
      name: rule.name, feature_type: rule.feature_type, condition: rule.condition,
      threshold: rule.threshold, window_minutes: rule.window_minutes,
      cooldown_minutes: rule.cooldown_minutes, prompt_goal: rule.prompt_goal,
    })
  }

  const handleSaveEdit = async () => {
    if (!editRule.name?.trim() || !editRule.feature_type?.trim()) return
    const res = await api.updateTriggerRule(editingId, {
      ...editRule,
      threshold: parseFloat(editRule.threshold) || 0,
      window_minutes: parseInt(editRule.window_minutes) || 60,
      cooldown_minutes: parseInt(editRule.cooldown_minutes) || 30,
    })
    if (!res.success) { setFlash('error', res.error || t('common.unknown_error')); return }
    setEditingId(null)
    fetchRules()
  }

  return (
    <div className="card !p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="section-title flex items-center gap-2">
          <Zap className="w-5 h-5 text-warn" />
          {t('agent.trigger_rules')}
        </h3>
        <button type="button" onClick={() => setShowAdd(!showAdd)} aria-expanded={showAdd} className="text-xs flex items-center gap-1 text-primary-600 hover:text-primary-800">
          <Plus className="w-3 h-3" /> {t('common.add')}
        </button>
      </div>
      <InlineFlash flash={flash} className="mb-3" />
      {loadError && (
        <div role="alert" className="mb-3 flex items-center gap-2 text-xs text-bad-ink">
          <span className="flex-1">{loadError}</span>
          <button type="button" onClick={fetchRules} className="underline">{t('common.retry')}</button>
        </div>
      )}

      {showAdd && (
        <div className="mb-3 p-3 bg-sunken rounded-chip border border-line space-y-2">
          <input type="text" value={newRule.name} onChange={(e) => setNewRule({ ...newRule, name: e.target.value })} placeholder={t('agent.rule_name')} aria-label={t('agent.rule_name')} className="input w-full text-sm" />
          <div className="grid grid-cols-2 gap-2">
            <input type="text" value={newRule.feature_type} onChange={(e) => setNewRule({ ...newRule, feature_type: e.target.value })} placeholder={t('agent.feature_placeholder')} aria-label={t('agent.feature_placeholder')} className="input text-sm" />
            <ConditionSelect value={newRule.condition} onChange={(e) => setNewRule({ ...newRule, condition: e.target.value })} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <input type="number" value={newRule.threshold} onChange={(e) => setNewRule({ ...newRule, threshold: e.target.value })} placeholder={t('agent.threshold')} aria-label={t('agent.threshold')} className="input text-sm" />
            <input type="number" value={newRule.window_minutes} onChange={(e) => setNewRule({ ...newRule, window_minutes: e.target.value })} placeholder={t('agent.window_min')} aria-label={t('agent.window_min')} className="input text-sm" />
            <input type="number" value={newRule.cooldown_minutes} onChange={(e) => setNewRule({ ...newRule, cooldown_minutes: e.target.value })} placeholder={t('agent.cooldown_min')} aria-label={t('agent.cooldown_min')} className="input text-sm" />
          </div>
          <textarea value={newRule.prompt_goal} onChange={(e) => setNewRule({ ...newRule, prompt_goal: e.target.value })} placeholder={t('agent.triggered_when_placeholder')} aria-label={t('agent.triggered_when_placeholder')} className="input w-full text-sm" rows={2} />
          <div className="flex gap-2">
            <button onClick={handleCreate} className="btn btn-primary text-xs px-3 py-1">{t('common.create')}</button>
            <button onClick={() => setShowAdd(false)} className="btn text-xs px-3 py-1">{t('common.cancel')}</button>
          </div>
        </div>
      )}

      {rules.length === 0 ? (
        <p className="text-sm text-ink-3 text-center py-4">{t('agent.no_trigger_rules')}</p>
      ) : (
        <div className="space-y-2">
          {rules.map((rule) => (
            editingId === rule.id ? (
              <div key={rule.id} className="p-2.5 rounded-chip border border-primary-200 bg-primary-50/30 text-sm space-y-2">
                <input type="text" value={editRule.name} onChange={(e) => setEditRule({ ...editRule, name: e.target.value })} className="input w-full text-sm" placeholder={t('agent.rule_name')} aria-label={t('agent.rule_name')} />
                <div className="grid grid-cols-2 gap-2">
                  <input type="text" value={editRule.feature_type} onChange={(e) => setEditRule({ ...editRule, feature_type: e.target.value })} className="input text-sm" placeholder={t('agent.feature_type')} aria-label={t('agent.feature_type')} />
                  <ConditionSelect value={editRule.condition} onChange={(e) => setEditRule({ ...editRule, condition: e.target.value })} />
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <input type="number" value={editRule.threshold} onChange={(e) => setEditRule({ ...editRule, threshold: e.target.value })} placeholder={t('agent.threshold')} aria-label={t('agent.threshold')} className="input text-sm" />
                  <input type="number" value={editRule.window_minutes} onChange={(e) => setEditRule({ ...editRule, window_minutes: e.target.value })} placeholder={t('agent.window_min')} aria-label={t('agent.window_min')} className="input text-sm" />
                  <input type="number" value={editRule.cooldown_minutes} onChange={(e) => setEditRule({ ...editRule, cooldown_minutes: e.target.value })} placeholder={t('agent.cooldown_min')} aria-label={t('agent.cooldown_min')} className="input text-sm" />
                </div>
                <textarea value={editRule.prompt_goal} onChange={(e) => setEditRule({ ...editRule, prompt_goal: e.target.value })} className="input w-full text-sm" rows={2} placeholder={t('agent.analysis_goal_placeholder')} aria-label={t('agent.analysis_goal_placeholder')} />
                <div className="flex gap-1">
                  <button onClick={handleSaveEdit} className="p-1 hover:bg-ok/10 rounded-chip" title={t('common.save')} aria-label={t('common.save')}><Check className="w-3.5 h-3.5 text-ok-ink" /></button>
                  <button onClick={() => setEditingId(null)} className="p-1 hover:bg-sunken rounded-chip" title={t('common.cancel')} aria-label={t('common.cancel')}><X className="w-3.5 h-3.5 text-ink-3" /></button>
                </div>
              </div>
            ) : (
              <div key={rule.id} className={`p-2.5 rounded-chip border text-sm ${rule.status === 'active' ? 'bg-panel border-line' : 'bg-sunken border-line opacity-60'}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-xs text-ink">{rule.name}</span>
                      <span className={`text-[10px] font-bold uppercase px-1.5 rounded-chip ${rule.status === 'active' ? 'bg-ok/15 text-ok-ink' : 'bg-warn/15 text-warn-ink'}`}>
                        {t(`agent.status_${rule.status}`, rule.status)}
                      </span>
                    </div>
                    <div className="text-xs text-ink-2 font-mono mb-0.5">
                      {rule.feature_type} {CONDITION_SYMBOLS[rule.condition] || t(`agent.cond_short_${rule.condition}`, rule.condition)} {rule.threshold}
                      <span className="text-ink-3 ml-2">{t('agent.rule_window_cooldown', { window: rule.window_minutes, cooldown: rule.cooldown_minutes })}</span>
                    </div>
                    <p className="text-ink text-xs truncate">{rule.prompt_goal}</p>
                    {rule.trigger_count > 0 && (
                      <div className="text-[10px] text-ink-3 mt-0.5">{t('agent.triggered_times', { count: rule.trigger_count })}</div>
                    )}
                  </div>
                  <div className="flex gap-1 flex-shrink-0">
                    {isRunning && (
                      <button onClick={() => handleTrigger(rule)} className="p-1 hover:bg-info/10 rounded-chip" title={t('agent.run_now')} aria-label={t('agent.run_now')}>
                        <Play className="w-3.5 h-3.5 text-info" />
                      </button>
                    )}
                    <button onClick={() => handleEdit(rule)} className="p-1 hover:bg-info/10 rounded-chip" title={t('common.edit')} aria-label={t('common.edit')}>
                      <Pencil className="w-3.5 h-3.5 text-info" />
                    </button>
                    <button onClick={() => handleToggle(rule)} className="p-1 hover:bg-warn/10 rounded-chip" title={rule.status === 'active' ? t('agent.pause') : t('agent.resume')} aria-label={rule.status === 'active' ? t('agent.pause') : t('agent.resume')}>
                      {rule.status === 'active' ? <Pause className="w-3.5 h-3.5 text-warn" /> : <RotateCcw className="w-3.5 h-3.5 text-ok" />}
                    </button>
                    <button onClick={() => handleDelete(rule)} className="p-1 hover:bg-bad/10 rounded-chip" title={t('common.delete')} aria-label={t('common.delete')}>
                      <Trash2 className="w-3.5 h-3.5 text-bad" />
                    </button>
                  </div>
                </div>
              </div>
            )
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Startup Progress Modal
// ---------------------------------------------------------------------------

const STARTUP_STEPS = [
  { key: 1, labelKey: 'agent.startup_creating_llm', icon: Cpu },
  { key: 2, labelKey: 'agent.startup_init_health',  icon: HardDrive },
  { key: 3, labelKey: 'agent.startup_init_memory',  icon: Database },
  { key: 4, labelKey: 'agent.startup_building',     icon: Brain },
  { key: 5, labelKey: 'agent.startup_ingestion',    icon: Server },
  { key: 6, labelKey: 'agent.startup_tasks',        icon: ListChecks },
  { key: 7, labelKey: 'agent.startup_started',      icon: Rocket },
]

function StartupModal({ currentStep, error, onClose }) {
  const { t } = useTranslation()
  const dialogRef = useRef(null)
  // Animate through steps progressively even when they arrive in a burst.
  const [displayStep, setDisplayStep] = useState(0)
  useEffect(() => {
    if (currentStep <= displayStep) return
    // Advance one step at a time with a short delay for visual feedback
    const timer = setTimeout(() => {
      setDisplayStep((prev) => prev + 1)
    }, currentStep - displayStep > 3 ? 120 : 250)
    return () => clearTimeout(timer)
  }, [currentStep, displayStep])

  const done = displayStep > 7
  const closable = done || !!error

  // Move focus into the dialog on open so keyboard users land inside it.
  useEffect(() => {
    dialogRef.current?.focus()
  }, [])

  const onKeyDown = (e) => {
    if (e.key === 'Escape' && closable) onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onKeyDown={onKeyDown}>
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="startup-modal-title"
        className="bg-panel rounded-card shadow-2xl w-full max-w-md mx-4 overflow-hidden outline-none"
      >
        {/* Header */}
        <div className={`px-6 py-4 ${error ? 'bg-bad/10' : done ? 'bg-ok/10' : 'bg-info/10'}`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              {error ? (
                <AlertCircle className="w-6 h-6 text-bad" />
              ) : done ? (
                <CheckCircle2 className="w-6 h-6 text-ok" />
              ) : (
                <Loader2 className="w-6 h-6 text-info animate-spin" />
              )}
              <h3 id="startup-modal-title" className="section-title">
                {error ? t('agent.startup_failed') : done ? t('agent.agent_ready') : t('agent.starting_agent')}
              </h3>
            </div>
            {(done || error) && (
              <button type="button" onClick={onClose} aria-label={t('common.close')} className="text-ink-3 hover:text-ink-2 transition-colors">
                <X className="w-5 h-5" />
              </button>
            )}
          </div>
        </div>

        {/* Steps */}
        <div className="px-6 py-5 space-y-1">
          {STARTUP_STEPS.map(({ key, labelKey, icon: Icon }) => {
            const completed = displayStep > key
            const active = displayStep === key && !error
            const pending = displayStep < key
            return (
              <div key={key} className={`flex items-center space-x-3 py-2 px-3 rounded-control transition-all duration-300 ${
                active ? 'bg-info/10' : completed ? 'bg-sunken' : ''
              }`}>
                <div className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition-all duration-300 ${
                  completed ? 'bg-ok/15' : active ? 'bg-info/15' : 'bg-sunken'
                }`}>
                  {completed ? (
                    <Check className="w-4 h-4 text-ok-ink" />
                  ) : active ? (
                    <Loader2 className="w-4 h-4 text-info-ink animate-spin" />
                  ) : (
                    <Icon className={`w-4 h-4 ${pending ? 'text-ink-3' : 'text-ink-3'}`} />
                  )}
                </div>
                <span className={`text-sm transition-colors duration-300 ${
                  completed ? 'text-ink-2' : active ? 'text-info-ink font-medium' : 'text-ink-3'
                }`}>
                  {t(labelKey)}
                </span>
              </div>
            )
          })}
        </div>

        {/* Error message */}
        {error && (
          <div className="px-6 pb-4">
            <div className="bg-bad/10 border border-bad/30 rounded-control p-3">
              <p className="text-sm text-bad-ink font-mono break-all">{error}</p>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-6 pb-5">
          {done ? (
            <button onClick={onClose}
              className="w-full py-2.5 bg-ok hover:bg-ok/90 text-panel rounded-control font-medium transition-colors">
              {t('common.done')}
            </button>
          ) : error ? (
            <button onClick={onClose}
              className="w-full py-2.5 bg-ink-2 hover:bg-ink text-panel rounded-control font-medium transition-colors">
              {t('common.dismiss')}
            </button>
          ) : (
            <div className="w-full bg-line rounded-full h-1.5 overflow-hidden">
              <div className="bg-info h-full rounded-full transition-all duration-500 ease-out"
                style={{ width: `${Math.max(5, ((displayStep - 1) / 7) * 100)}%` }} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Monitor
// ---------------------------------------------------------------------------

/** Max log entries kept on screen. */
const MAX_LOG_ITEMS = 500
/** Right after a (re)connect the server replays its recent backlog; events seen in this window are de-duplicated. */
const REPLAY_WINDOW_MS = 3000
const EMPTY_LIVE = { thinking: '', content: '' }
/** Max timeline records kept (history fetch + live). */
const MAX_TIMELINE = 1500
/** Agent states in which nothing is being worked on. */
const IDLE_STATES = new Set(['', 'idle', 'initialized', 'chat_complete', 'chat_suspended'])
const stripToolCallXml = (text) => (text || '').replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '').trim()

export default function AutonomousAgentMonitor({ active = true }) {
  const { t } = useTranslation()
  // Stable actions only: the sidebar's running indicator reads agentStatus, so
  // this page keeps it in sync with what it polls — without re-rendering on
  // every live data batch.
  const { dispatch: appDispatch } = useAppActions()
  const visible = useDocumentVisible()
  const [actionFlash, setActionFlash] = useFlash(6000)
  // State
  const [agentStatus, setAgentStatus] = useState(null)
  const [isRunning, setIsRunning] = useState(false)
  const [logUpdates, setLogUpdates] = useState([])
  const [logLoadError, setLogLoadError] = useState('')
  const [logFilter, setLogFilter] = useState('all') // 'all' | 'analysis' | 'chat'
  const [llmProvider, setLlmProvider] = useState('gemini')
  const [model, setModel] = useState('')
  // Only the setter is used — the backend default is stored but never rendered.
  const [, setDefaultModel] = useState('')
  const [providerModels, setProviderModels] = useState({})
  const [wsConnected, setWsConnected] = useState(false)
  const [cumulativeTokens, setCumulativeTokens] = useState({ prompt: 0, thoughts: 0, response: 0, cacheRead: 0, cacheCreation: 0 })
  // Chronological timeline records (the primary view) + view switch.
  const [timeline, setTimeline] = useState([])
  const [activityLoaded, setActivityLoaded] = useState(false)
  const [view, setView] = useState('activity') // 'activity' | 'raw'
  const [lastError, setLastError] = useState('')
  const [stoppingReply, setStoppingReply] = useState(false)
  // Streaming text (latest thought, reply being typed) lives outside React state
  // so a streamed delta re-renders only the live bubble.
  const [liveStore] = useState(createLiveStore)
  const [stopping, setStopping] = useState(false)

  const [wsReconnecting, setWsReconnecting] = useState(false)
  const [startupModal, setStartupModal] = useState(null) // null | { step, error }

  // Refs
  const wsRef = useRef(null)
  const wsStateRef = useRef('disconnected') // 'disconnected' | 'connecting' | 'connected'
  const wsReconnectTimerRef = useRef(null)
  const wsReconnectAttemptsRef = useRef(0)
  const configSyncedRef = useRef(false) // track whether we've synced config from running agent
  const configHydratedRef = useRef(false) // true once the initial provider/model resolution finished
  const connectMonitorRef = useRef(null) // stable ref for reconnect to call
  const isRunningRef = useRef(false) // track isRunning for WS onclose to check
  // True while a start this tab triggered is in flight (gates the startup modal).
  const startInitiatedRef = useRef(false)
  // Streaming chunks bucketed by taskType (analysis/chat/quick/scheduled) so
  // concurrent loops don't interleave their chunks into the same string.
  const streamBufferRef = useRef({})
  const lastStreamTaskRef = useRef('analysis')
  // Replay de-duplication: how many times each event key is already on screen,
  // and (right after a connect) how many of those the server may still replay.
  const keyCountsRef = useRef(new Map())
  const replayBudgetRef = useRef(null)
  const replayTimerRef = useRef(null)
  const waitingShownRef = useRef(false)

  // Narration (chat_content / content) buffered per run until a tool call
  // follows, when it is demoted to a "thought" row in the timeline.
  const thoughtBufRef = useRef({})

  const clearLive = useCallback(() => {
    liveStore.setThought('')
  }, [liveStore])

  /** Append one record to the timeline. */
  const pushRecord = useCallback((rec) => {
    if (!rec) return
    setTimeline((prev) => {
      const next = [...prev, rec]
      return next.length > MAX_TIMELINE ? next.slice(next.length - MAX_TIMELINE) : next
    })
  }, [])

  /** Remember that an event with this key is on screen (bounded). */
  const registerKey = useCallback((key) => {
    const counts = keyCountsRef.current
    if (counts.size > 5000) counts.clear()
    counts.set(key, (counts.get(key) || 0) + 1)
  }, [])

  /** True when `key` is a backlog replay of something already shown (consumes one budget slot). */
  const consumeReplay = useCallback((key) => {
    const budget = replayBudgetRef.current
    const left = budget ? budget.get(key) || 0 : 0
    if (left <= 0) return false
    budget.set(key, left - 1)
    return true
  }, [])

  // Flush accumulated streaming content. If taskType is given, flush only that
  // bucket; otherwise flush every bucket.
  const flushStreamBuffer = useCallback((taskType) => {
    const buckets = streamBufferRef.current
    const targets = taskType ? [taskType] : Object.keys(buckets)
    const now = Date.now()
    const time = new Date(now).toLocaleTimeString()
    const toAdd = []
    for (const tt of targets) {
      const buf = buckets[tt]
      if (!buf) continue
      // The list is newest-first. Thinking precedes its reply, so the reply is
      // listed first (on top) and the thinking right below it.
      const contentStripped = stripToolCallXml(buf.content)
      if (contentStripped) {
        toAdd.push({ id: genId(), time, ts: now, message: { text: `🤖 ${t('agent.evt_assistant')}: ${contentStripped}`, type: 'content', taskType: tt } })
      }
      if (buf.thinking) {
        toAdd.push({ id: genId(), time, ts: now, message: { text: `💭 ${buf.thinking}`, type: 'thinking', taskType: tt } })
      }
      buf.content = ''
      buf.thinking = ''
    }
    if (toAdd.length > 0) {
      setLogUpdates((prev) => [...toAdd, ...prev.slice(0, MAX_LOG_ITEMS - toAdd.length)])
    }
    clearLive()
  }, [clearLive, t])

  /** meta: { ts?: epoch ms of the event, key?: dedupe key } */
  const addStatusUpdate = useCallback((msgObj, meta = {}) => {
    const { taskType, ...message } = msgObj
    const tt = taskType || 'analysis'
    const buckets = streamBufferRef.current
    if (!buckets[tt]) buckets[tt] = { content: '', thinking: '' }
    const buf = buckets[tt]

    if (message.isStreaming) {
      const delta = message.rawDelta ?? message.text?.replace(/^💭 |^🤖 [^:]*: /, '') ?? ''
      lastStreamTaskRef.current = tt
      if (message.type === 'thinking') {
        if (buf.content) flushStreamBuffer(tt)
        buf.thinking += delta
      } else if (message.type === 'content') {
        if (buf.thinking) flushStreamBuffer(tt)
        buf.content += delta
      }
      return
    }

    // Non-streaming event: flush only its own bucket so concurrent loops keep
    // their in-flight streaming text intact.
    flushStreamBuffer(tt)
    const ts = meta.ts ?? Date.now()
    const update = { id: genId(), time: new Date(ts).toLocaleTimeString(), ts, key: meta.key, message: { ...message, taskType: tt } }
    setLogUpdates((prev) => {
      // Consecutive progress ticks from the same tool replace each other.
      if (message.type === 'progress' && prev[0]?.message?.type === 'progress' && prev[0].message.progressTool === message.progressTool) {
        return [update, ...prev.slice(1)]
      }
      return [update, ...prev.slice(0, MAX_LOG_ITEMS - 1)]
    })
  }, [flushStreamBuffer])

  const addCumulativeTokens = useCallback((tu) => {
    if (!tu) return
    setCumulativeTokens((prev) => ({
      prompt: prev.prompt + (tu.prompt_tokens ?? 0),
      thoughts: prev.thoughts + (tu.thoughts_tokens ?? 0),
      response: prev.response + (tu.response_tokens ?? tu.completion_tokens ?? 0),
      cacheRead: (prev.cacheRead ?? 0) + (tu.cache_read_tokens ?? 0),
      cacheCreation: (prev.cacheCreation ?? 0) + (tu.cache_creation_tokens ?? 0),
    }))
  }, [])

  // Timer to sync streaming buffer to live preview state (real-time display).
  // Single-channel preview shows the most recently active bucket. Paused while
  // the page is hidden or not the active route; unchanged content bails out of
  // the state update so an idle stream causes no re-renders.
  useEffect(() => {
    if (!active || !visible) return undefined
    const timer = setInterval(() => {
      const tt = lastStreamTaskRef.current || 'analysis'
      const buf = streamBufferRef.current[tt] || EMPTY_LIVE
      // Latest narration wins over reasoning; either is shown as one muted line.
      const thought = stripToolCallXml(buf.content) || (buf.thinking || '').trim()
      liveStore.setThought(thought.length > 600 ? thought.slice(-600) : thought)
    }, 150)
    return () => clearInterval(timer)
  }, [active, visible, liveStore])

  const formatAgentState = (status) => {
    if (!status) return '—'
    const s = status.state
    const dur = Math.round(status.state_duration || 0)
    if (!s || s === 'idle') return `⏳ ${t('agent.state_idle', { dur })}`
    if (s === 'thinking') return `🤔 ${t('agent.state_thinking', { dur })}`
    if (s === 'thinking_retry') return `🔄 ${t('agent.state_retry', { dur })}`
    if (s === 'initialized') return `🏁 ${t('agent.state_ready')}`
    if (s === 'chat_processing') return `💬 ${t('agent.state_chat', { dur })}`
    if (s === 'chat_thinking') return `💬 ${t('agent.state_chat_thinking', { dur })}`
    if (s === 'chat_complete') return `💬 ${t('agent.state_chat_done')}`
    if (s === 'chat_suspended') return `💬 ${t('agent.state_chat_suspended')}`
    if (s === 'quick_analysis') return `⚡ ${t('agent.state_quick_analysis', { dur })}`
    if (s.startsWith('executing:') || s.startsWith('chat_executing:')) {
      const tool = s.split(':')[1]
      return `⚙️ ${t('agent.state_executing', { tool, dur })}`
    }
    return `${s.charAt(0).toUpperCase() + s.slice(1)} (${dur}s)`
  }

  // API Calls
  const checkAgentStatus = useCallback(async () => {
    try {
      const result = await api.getAgentStatus('LiveUser')
      if (result.success && result.running) {
        setAgentStatus(result)
        setIsRunning(true)
        appDispatch({ type: 'SET_AGENT_STATUS', payload: { ...result, running: true, user_id: 'LiveUser' } })
        if (result.status?.cumulative_tokens) {
          const ct = result.status.cumulative_tokens
          setCumulativeTokens({
            prompt: ct.prompt_tokens || 0,
            thoughts: ct.thoughts_tokens || 0,
            response: ct.completion_tokens || ct.response_tokens || 0,
            cacheRead: ct.cache_read_tokens || 0,
            cacheCreation: ct.cache_creation_tokens || 0,
          })
        }
        // Sync LLM provider/model from running agent config on first successful poll
        if (!configSyncedRef.current && result.config) {
          configSyncedRef.current = true
          if (result.config.llm_provider) setLlmProvider(result.config.llm_provider)
          if (result.config.model) setModel(result.config.model)
        }
      } else if (result.success) {
        // Only an explicit "not running" answer means stopped.
        setAgentStatus(null)
        setIsRunning(false)
        setWsReconnecting(false)
        configSyncedRef.current = false
        appDispatch({ type: 'SET_AGENT_STATUS', payload: { running: false } })
      }
      // A failed poll (network blip, 5xx, 401) keeps the previous state.
    } catch (error) {
      console.error('Failed to get agent status:', error)
    }
  }, [appDispatch])

  const fetchActivityLog = useCallback(async () => {
    try {
      const result = await api.getAgentActivity(MAX_LOG_ITEMS)
      if (!result.success) {
        setLogLoadError(result.error || t('common.load_failed'))
        return
      }
      setLogLoadError('')
      if (!result.events?.length) return
      const items = []
      const recs = []
      const fetchedCounts = new Map()
      result.events.forEach((ev) => {
        const d = unwrapEvent(ev)
        const type = ev.type || d.type || ''
        const key = eventKey(type, d)
        fetchedCounts.set(key, (fetchedCounts.get(key) || 0) + 1)
        const msg = eventToMessage(ev)
        const ts = eventTimeMs(ev, parseBackendDate) ?? Date.now()
        const rec = toTimelineRecord(type, d, { id: genId(), key, ts, note: msg?.text })
        if (rec) recs.push(rec)
        if (!msg) return
        if (msg.isStreaming) return
        items.push({ id: genId(), time: new Date(ts).toLocaleTimeString(), ts, key, message: msg })
      })
      // Everything fetched is now "on screen": a WS backlog replay of it must be skipped.
      const counts = keyCountsRef.current
      for (const [k, n] of fetchedCounts) counts.set(k, Math.max(counts.get(k) || 0, n))
      // Merge rather than overwrite: live events that arrived while the fetch
      // was in flight (or streamed text that is never persisted) must survive.
      setLogUpdates((prev) => mergeActivity(prev, items.reverse(), MAX_LOG_ITEMS))
      // History is chronological already; live records that raced the fetch are kept.
      setTimeline((prev) => mergeChrono(prev, recs, MAX_TIMELINE))
    } catch (e) {
      setLogLoadError(e?.message || t('common.load_failed'))
    } finally {
      setActivityLoaded(true)
    }
  }, [t])

  /**
   * Feed one (already de-duplicated) live event into the timeline. Narration
   * deltas are buffered per run and demoted to a "thought" row when a tool
   * call follows; a delivered reply supersedes its streamed draft.
   */
  const ingestTimeline = useCallback((type, d, ts, key, note) => {
    const isChat = type.startsWith('chat_') || type === 'user_message'
    const bufKey = isChat ? (d.run_id || 'chat') : 'bg'
    const bufs = thoughtBufRef.current
    if (type === 'chat_content' || type === 'content') {
      bufs[bufKey] = (bufs[bufKey] || '') + (d.content || '')
      return
    }
    if (type === 'chat_thinking' || type === 'agent_thinking') return
    if (/^(chat|analysis|quick|plan)_tool_call$/.test(type)) {
      const text = stripToolCallXml(bufs[bufKey])
      bufs[bufKey] = ''
      if (text && d.tool !== 'reply_user' && d.tool !== 'finish_chat') {
        pushRecord(toTimelineRecord('thought', { content: text, run_id: d.run_id, scope: isChat ? 'chat' : 'bg' }, { id: genId(), ts: ts - 1 }))
      }
    } else if (['chat_reply', 'chat_stopped', 'chat_cleared', 'user_message', 'cycle_start', 'cycle_end'].includes(type)) {
      bufs[bufKey] = ''
    }
    if (type === 'chat_reply') liveStore.setReply(d.run_id || '_', '')
    if (type === 'chat_stopped') {
      liveStore.clearReplies()
      liveStore.setThought('')
      streamBufferRef.current.chat = { content: '', thinking: '' }
    }
    if (type === 'agent_started' || type === 'cycle_start' || type === 'user_message' || type === 'agent_stopped') setLastError('')
    if (type === 'agent_error' || type === 'startup_error') setLastError(d.error || note || '')
    pushRecord(toTimelineRecord(type, d, { id: genId(), key, ts, note }))
  }, [liveStore, pushRecord])

  // Schedule a WebSocket reconnect with exponential backoff
  const scheduleReconnect = useCallback(() => {
    if (wsReconnectTimerRef.current) return // already scheduled
    const attempts = wsReconnectAttemptsRef.current
    const delay = Math.min(2000 * Math.pow(1.5, attempts), 15000) // 2s -> 15s max
    wsReconnectAttemptsRef.current = attempts + 1
    setWsReconnecting(true)
    wsReconnectTimerRef.current = setTimeout(() => {
      wsReconnectTimerRef.current = null
      if (connectMonitorRef.current) connectMonitorRef.current()
    }, delay)
  }, [])

  // WebSocket connection
  const connectMonitor = useCallback(() => {
    if (wsStateRef.current === 'connecting' || wsStateRef.current === 'connected') return

    wsStateRef.current = 'connecting'
    if (wsRef.current) { const old = wsRef.current; wsRef.current = null; old.close() }

    const websocket = api.connectAgentMonitor()

    websocket.onopen = () => {
      wsStateRef.current = 'connected'
      wsReconnectAttemptsRef.current = 0
      waitingShownRef.current = false
      setWsConnected(true)
      setWsReconnecting(false)
      // The server replays its recent backlog on connect: anything already on
      // screen (activity-log fetch, or a previous connection) is skipped for a moment.
      replayBudgetRef.current = new Map(keyCountsRef.current)
      if (replayTimerRef.current) clearTimeout(replayTimerRef.current)
      replayTimerRef.current = setTimeout(() => {
        replayTimerRef.current = null
        replayBudgetRef.current = null
      }, REPLAY_WINDOW_MS)
      addStatusUpdate({ taskType: 'analysis', text: `📡 ${t('agent.monitor_connected')}`, type: 'system' })
    }

    websocket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        if (data.type === 'status_update') {
          setAgentStatus((prev) => ({
            ...prev,
            success: true,
            running: true,
            status: data.status,
            ...(data.data_store_stats && { data_store_stats: data.data_store_stats })
          }))
          if (data.status?.cumulative_tokens) {
            const ct = data.status.cumulative_tokens
            setCumulativeTokens({
              prompt: ct.prompt_tokens || 0,
              thoughts: ct.thoughts_tokens || 0,
              response: ct.completion_tokens || ct.response_tokens || 0,
              cacheRead: ct.cache_read_tokens || 0,
              cacheCreation: ct.cache_creation_tokens || 0,
            })
          }
          return
        }
        if (data.type === 'pong') return
        if (data.type === 'agent_waiting') {
          // Connected, but the agent hasn't started yet. Not an error; events
          // follow (agent_started, …) once it does.
          if (!waitingShownRef.current) {
            waitingShownRef.current = true
            addStatusUpdate({ taskType: 'analysis', text: `📡 ${t('agent.evt_waiting')}`, type: 'system' })
            pushRecord(toTimelineRecord('agent_waiting', data, { id: genId(), ts: Date.now(), note: t('agent.evt_waiting') }))
          }
          return
        }
        if (data.type === 'chat_reply_delta') {
          // Full reply text so far (not a diff). Streamed straight into the live
          // store; never logged or de-duplicated (every snapshot is distinct).
          liveStore.setReply(data.run_id || '_', data.reset ? '' : (data.text || ''))
          return
        }

        // De-duplicate the backlog replay against what is already on screen.
        const key = eventKey(data.type || '', data)
        if (consumeReplay(key)) return
        registerKey(key)
        const evTs = eventTimeMs(data, parseBackendDate) ?? Date.now()

        if (data.type === 'token_usage') {
          const isChat = !!data.chat_id
          const tokenUsage = {
            prompt_tokens: data.prompt_tokens,
            completion_tokens: data.completion_tokens,
            thoughts_tokens: data.thoughts_tokens,
            response_tokens: data.response_tokens,
            cache_read_tokens: data.cache_read_tokens,
            cache_creation_tokens: data.cache_creation_tokens,
          }
          addCumulativeTokens(tokenUsage)
          ingestTimeline('token_usage', data, evTs, key, '')
          const tt = isChat ? 'chat' : 'analysis'
          const buckets = streamBufferRef.current
          if (!buckets[tt]) buckets[tt] = { content: '', thinking: '' }
          const buf = buckets[tt]
          const now = Date.now()
          const time = new Date(now).toLocaleTimeString()
          clearLive()
          const toPrepend = []
          // Newest-first: the reply goes on top, its thinking right below it.
          const contentStripped = stripToolCallXml(buf.content)
          if (contentStripped) {
            toPrepend.push({ id: genId(), time, ts: now, message: { text: `🤖 ${t('agent.evt_assistant')}: ${contentStripped}`, type: 'content', taskType: tt } })
          }
          if (buf.thinking) {
            toPrepend.push({ id: genId(), time, ts: now, message: { text: `💭 ${buf.thinking}`, type: 'thinking', taskType: tt } })
          }
          buf.thinking = ''
          buf.content = ''
          setLogUpdates((prev) => {
            const withFlushed = toPrepend.length ? [...toPrepend, ...prev] : prev
            // The list is newest-first, so scan forward to find the most
            // recent reply — that's the one this usage belongs to.
            let idx = -1
            for (let i = 0; i < withFlushed.length; i++) {
              if (withFlushed[i].message?.type === 'content') { idx = i; break }
            }
            if (idx >= 0) {
              const next = withFlushed === prev ? [...prev] : withFlushed
              next[idx] = { ...next[idx], message: { ...next[idx].message, tokenUsage } }
              return next.length > MAX_LOG_ITEMS ? next.slice(0, MAX_LOG_ITEMS) : next
            }
            const tokenLine = formatTokenUsage(tokenUsage)
            if (tokenLine) {
              return [{ id: genId(), time, ts: now, message: { text: tokenLine, type: 'token_usage', tokenUsage, taskType: isChat ? 'chat' : 'analysis' } }, ...withFlushed.slice(0, MAX_LOG_ITEMS - 1)]
            }
            return withFlushed.length > MAX_LOG_ITEMS ? withFlushed.slice(0, MAX_LOG_ITEMS) : withFlushed
          })
        } else {
          if (data.type === 'startup_progress') {
            // Only pop the modal for a start this tab initiated, or when the
            // agent wasn't running — never for a stale replayed progress step.
            if (startInitiatedRef.current || !isRunningRef.current) {
              setStartupModal({ step: data.step, error: null })
            }
          } else if (data.type === 'agent_started') {
            // Mark startup complete (step 8 = past the last step)
            setStartupModal((prev) => prev ? { step: 8, error: null } : null)
            streamBufferRef.current = {}
            thoughtBufRef.current = {}
            lastStreamTaskRef.current = 'analysis'
            liveStore.reset()
            checkAgentStatus()
          } else if (data.type === 'startup_error') {
            setStartupModal((prev) => ({ step: prev?.step || 0, error: data.error || t('common.unknown_error') }))
            setIsRunning(false)
            setWsReconnecting(false)
          }
          const msg = eventToMessage(data)
          ingestTimeline(data.type || '', data, evTs, key, msg?.text)
          if (msg) addStatusUpdate(msg, { ts: evTs, key })
        }
      } catch (e) {
        console.error('Failed to parse monitor event:', e)
      }
    }

    websocket.onerror = () => {
      if (wsRef.current !== websocket) return
      wsStateRef.current = 'disconnected'
      setWsConnected(false)
    }
    websocket.onclose = () => {
      const wasOurs = wsRef.current === websocket
      if (!wasOurs) return
      wsStateRef.current = 'disconnected'
      setWsConnected(false)
      if (isRunningRef.current) {
        addStatusUpdate({ taskType: 'analysis', text: `📡 ${t('agent.monitor_disconnected')}`, type: 'system' })
        // Auto-reconnect only if agent is still believed to be running
        scheduleReconnect()
      }
    }
    wsRef.current = websocket
  }, [addStatusUpdate, addCumulativeTokens, checkAgentStatus, clearLive, consumeReplay, ingestTimeline, liveStore, pushRecord, registerKey, scheduleReconnect, t])

  // Keep stable refs for callbacks (updated after commit, never during render)
  useEffect(() => {
    connectMonitorRef.current = connectMonitor
    isRunningRef.current = isRunning
  }, [connectMonitor, isRunning])

  // Handlers
  const handleStartAgent = async () => {
    // Show modal immediately (step 0 = waiting for first progress event)
    startInitiatedRef.current = true
    setStartupModal({ step: 0, error: null })
    try {
      const result = await api.startAutonomousAgent(llmProvider, {
        model: model.trim() || undefined,
      })
      if (!result.success) {
        setStartupModal({ step: 0, error: result.error || result.detail || t('common.unknown_error') })
        return
      }
      setIsRunning(true)
      configSyncedRef.current = true
      wsReconnectAttemptsRef.current = 0
      addStatusUpdate({ taskType: 'analysis', text: `🚀 ${t('agent.agent_starting')}`, type: 'system' })
      connectMonitor()
      setTimeout(() => { checkAgentStatus() }, 2000)
    } catch (error) {
      setStartupModal({ step: 0, error: error.message || error.toString() })
    }
  }

  const handleStopAgent = async () => {
    if (stopping) return
    setStopping(true)
    try {
      // Ask the server first: only a confirmed stop may tear down local state,
      // otherwise a failed request would leave a running agent with no monitor.
      const res = await api.stopAutonomousAgent()
      if (!res.success) {
        setActionFlash('error', res.error || t('agent.stop_failed'))
        return
      }
      if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
      wsReconnectAttemptsRef.current = 0
      setWsReconnecting(false)
      if (wsRef.current) { const old = wsRef.current; wsRef.current = null; old.close() }
      wsStateRef.current = 'disconnected'
      setIsRunning(false)
      setAgentStatus(null)
      setWsConnected(false)
      configSyncedRef.current = false
      appDispatch({ type: 'SET_AGENT_STATUS', payload: { running: false } })
      addStatusUpdate({ taskType: 'analysis', text: `🛑 ${t('agent.agent_stopped_by_user')}`, type: 'system' })
      liveStore.reset()
      pushRecord(toTimelineRecord('agent_stopped', {}, { id: genId(), ts: Date.now(), note: t('agent.agent_stopped_by_user') }))
    } catch (error) {
      console.error('Failed to stop agent:', error)
      setActionFlash('error', error?.message || t('agent.stop_failed'))
    } finally {
      setStopping(false)
    }
  }

  // Effects
  useEffect(() => {
    // Resolve the initial (provider, model) by priority, lowest -> highest:
    //   1. Hardcoded useState defaults ('gemini', '')
    //   2. Backend /api/config/defaults (reflects DEFAULT_LLM_PROVIDER / .env)
    //   3. Persisted localStorage (user's last manual selection in this browser)
    //   4. Agent's last-run config (freshest source of truth)
    // Sequential so later layers win; each try/catch isolates failures.
    (async () => {
      try {
        const res = await api.getDefaults()
        if (res.model) setDefaultModel(res.model)
        if (res.provider_models) setProviderModels(res.provider_models)
        if (res.llm_provider) setLlmProvider(res.llm_provider)
      } catch (e) {
        console.warn('Failed to load backend defaults:', e)
      }

      const stored = loadStoredConfig()
      if (stored) {
        setLlmProvider(stored.llmProvider)
        setModel(stored.model)
      }

      try {
        const res = await api.getAgentLastConfig()
        if (res.success && res.config) {
          const cfg = res.config
          if (cfg.llm_provider) setLlmProvider(cfg.llm_provider)
          if (cfg.model) setModel(cfg.model)
        }
      } catch (e) {
        console.warn('Failed to load agent last config:', e)
      } finally {
        // Only from here on does (llmProvider, model) reflect a real choice
        // rather than the initial useState values.
        configHydratedRef.current = true
      }
    })()
  }, [])

  useEffect(() => {
    // Don't persist the pre-hydration defaults — that would clobber the
    // user's stored selection before it has even been read back.
    if (!configHydratedRef.current) return
    saveConfig({ llmProvider, model })
  }, [llmProvider, model])

  useEffect(() => {
    // False positive — both are async and only setState after their `await`
    // on the API call, so nothing is set synchronously in this effect body.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchActivityLog()
    checkAgentStatus()
  }, [fetchActivityLog, checkAgentStatus])

  // Poll agent status — the authoritative source for isRunning. Fast (5s)
  // while this page is on screen, slow (30s) in the background so the sidebar
  // indicator stays roughly right, and paused entirely while the tab is hidden.
  usePolling(checkAgentStatus, active ? 5000 : 30000, true)
  // Coming back to this page: catch up immediately.
  useOnActivate(active, () => { checkAgentStatus(); fetchActivityLog() })

  useEffect(() => {
    if (isRunning && wsStateRef.current === 'disconnected' && !wsReconnectTimerRef.current) {
      connectMonitor()
    }
    // If agent stopped, cancel any pending reconnect. Clearing the "reconnecting"
    // flag is done at each place that flips isRunning to false rather than here,
    // so this effect never setStates synchronously.
    if (!isRunning) {
      if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
      wsReconnectAttemptsRef.current = 0
    }
  }, [isRunning, connectMonitor])

  useEffect(() => {
    return () => {
      if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
      if (replayTimerRef.current) { clearTimeout(replayTimerRef.current); replayTimerRef.current = null }
      if (wsRef.current) { const old = wsRef.current; wsRef.current = null; old.close() }
      wsStateRef.current = 'disconnected'
    }
  }, [])

  // Filtered logs (raw view)
  const filteredLogs = useMemo(() => (
    logFilter === 'all'
      ? logUpdates
      : logUpdates.filter((u) => {
          const tt = u.message?.taskType || 'analysis'
          if (logFilter === 'chat') return tt === 'chat'
          return tt !== 'chat' // 'analysis' filter shows analysis + scheduled + quick + plan
        })
  ), [logUpdates, logFilter])

  const closeStartupModal = () => {
    startInitiatedRef.current = false
    setStartupModal(null)
  }

  // ── Timeline (memoised on the records; never on streamed text) ──────────
  const agentState = agentStatus?.status?.state || ''
  const idle = !isRunning || IDLE_STATES.has(agentState)
  const [now, setNow] = useState(() => Date.now())
  // A run with no end marker is declared finished shortly after the agent goes
  // idle (or stale after a while); nothing else would trigger that re-evaluation.
  useEffect(() => {
    if (!idle) return undefined
    const id = setTimeout(() => setNow(Date.now()), 8500)
    return () => clearTimeout(id)
  }, [idle, timeline])
  useEffect(() => {
    if (!active || !visible) return undefined
    const id = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(id)
  }, [active, visible])
  const items = useMemo(() => buildTimeline(timeline, { idle, now }), [timeline, idle, now])
  const liveRun = useMemo(() => (isRunning ? findLiveRun(items) : null), [items, isRunning])
  const liveStep = useMemo(() => currentStep(liveRun), [liveRun])
  const liveStepText = liveStep ? stepVerb(liveStep, t) : ''
  const chatLive = !!liveRun && liveRun.runType === 'chat'
  const stateBusy = isRunning && !IDLE_STATES.has(agentState)
  const showLive = isRunning && (!!liveRun || stateBusy)

  // ── Status hero ──────────────────────────────────────────────────────────
  const starting = (!!startupModal && !startupModal.error && startupModal.step < 8) || (isRunning && !agentStatus)
  let hero
  if (starting) {
    hero = { tone: 'warn', title: t('agent.hero_starting'), hint: '' }
  } else if (lastError) {
    hero = { tone: 'bad', title: t('agent.hero_error'), hint: lastError }
  } else if (!isRunning) {
    hero = { tone: 'off', title: t('agent.hero_stopped'), hint: t('agent.hero_stopped_hint') }
  } else if (showLive) {
    hero = liveStepText
      ? { tone: 'info', title: t('agent.hero_working', { step: liveStepText }), hint: stepObject(liveStep) }
      : { tone: 'info', title: stateBusy && !liveRun ? t('agent.hero_working_plain') : t('agent.hero_thinking'), hint: '' }
  } else {
    hero = { tone: 'ok', title: t('agent.hero_idle'), hint: t('agent.hero_idle_hint') }
  }
  const HERO_DOT = {
    ok: 'bg-ok', info: 'bg-info animate-pulse', warn: 'bg-warn animate-pulse', bad: 'bg-bad', off: 'bg-ink-3',
  }

  const handleStopReply = async () => {
    if (stoppingReply) return
    setStoppingReply(true)
    try {
      const res = await api.stopChat()
      if (!res.success) setActionFlash('error', res.error || t('agent.stop_reply_failed'))
      else if (!res.stopped) setActionFlash('success', t('agent.stop_reply_none'))
    } catch (error) {
      setActionFlash('error', error?.message || t('agent.stop_reply_failed'))
    } finally {
      setStoppingReply(false)
    }
  }

  const clearAll = () => {
    if (!window.confirm(t('agent.confirm_clear_logs'))) return
    setLogUpdates([])
    setTimeline([])
    liveStore.reset()
  }

  const hasTokens = cumulativeTokens.prompt > 0 || cumulativeTokens.thoughts > 0 || cumulativeTokens.response > 0
  const tokenTiles = [
    { label: t('agent.tok_input'), value: cumulativeTokens.prompt },
    { label: t('agent.tok_thinking'), value: cumulativeTokens.thoughts },
    { label: t('agent.tok_response'), value: cumulativeTokens.response },
    { label: t('agent.tok_cached'), value: cumulativeTokens.cacheRead || 0 },
  ]

  // Render
  return (
    <div className="space-y-4">
      {startupModal && (
        <StartupModal
          currentStep={startupModal.step}
          error={startupModal.error}
          onClose={closeStartupModal}
        />
      )}
      <div>
        <h2 className="page-title">{t('agent.title')}</h2>
        <p className="page-subtitle">{t('agent.subtitle')}</p>
      </div>

      {/* Status hero + agent controls */}
      <div className="card !p-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3" role="status" aria-live="polite" aria-atomic="true">
          <span className={`h-3 w-3 shrink-0 rounded-full ${HERO_DOT[hero.tone]}`} aria-hidden="true" />
          <div className="min-w-0">
            <div className="truncate text-lg font-semibold text-ink" data-testid="status-hero">{hero.title}</div>
            {hero.hint && <div className="truncate font-mono text-xs text-ink-2" title={hero.hint}>{hero.hint}</div>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 text-xs">
            {wsConnected ? (
              <><Wifi className="h-3 w-3 text-ok" aria-hidden="true" /><span className="text-ok-ink">{t('agent.live')}</span></>
            ) : isRunning && wsReconnecting ? (
              <><WifiOff className="h-3 w-3 animate-pulse text-warn" aria-hidden="true" /><span className="text-warn-ink">{t('agent.reconnecting')}</span></>
            ) : isRunning ? (
              <><WifiOff className="h-3 w-3 text-warn" aria-hidden="true" /><span className="text-warn-ink">{t('agent.polling')}</span></>
            ) : null}
          </div>
          {chatLive && (
            <button
              type="button"
              onClick={handleStopReply}
              disabled={stoppingReply}
              className="btn btn-secondary"
            >
              <Square className="h-3.5 w-3.5" aria-hidden="true" />
              {t('agent.stop_reply')}
            </button>
          )}
          <button
            type="button"
            onClick={isRunning ? handleStopAgent : handleStartAgent}
            disabled={stopping}
            className={`btn ${isRunning ? 'btn-danger' : 'btn-primary'}`}
          >
            {isRunning ? (<><Square className="h-4 w-4" aria-hidden="true" /><span>{t('agent.stop_agent')}</span></>) : (<><Play className="h-4 w-4" aria-hidden="true" /><span>{t('agent.start_agent')}</span></>)}
          </button>
        </div>
      </div>
      <InlineFlash flash={actionFlash} />

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-3">
        {/* Timeline (first on narrow screens, left 2/3 on wide) */}
        <div className="card order-1 flex h-[72dvh] min-h-[26rem] flex-col !p-3 sm:!p-4 lg:col-span-2 lg:h-[calc(100dvh-15rem)]">
          <div className="mb-3 flex flex-shrink-0 flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <h3 className="section-title flex items-center gap-2">
                <Activity className="h-4 w-4 text-primary-500" aria-hidden="true" />
                {t('agent.agent_activity')}
              </h3>
              <div className="ml-1 inline-flex rounded-control border border-line bg-sunken p-0.5" role="group" aria-label={t('agent.view_switch')}>
                {['activity', 'raw'].map((v) => (
                  <button
                    type="button"
                    key={v}
                    onClick={() => setView(v)}
                    aria-pressed={view === v}
                    className={`rounded-chip px-2.5 py-0.5 text-xs font-medium ${view === v ? 'bg-panel text-ink shadow-sm' : 'text-ink-3 hover:text-ink-2'}`}
                  >
                    {v === 'activity' ? t('agent.tab_activity') : t('agent.tab_raw')}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {view === 'raw' && ['all', 'analysis', 'chat'].map((f) => (
                <button
                  type="button"
                  key={f}
                  onClick={() => setLogFilter(f)}
                  aria-pressed={logFilter === f}
                  className={`rounded-chip px-2 py-0.5 text-xs font-medium ${logFilter === f ? 'bg-primary-100 text-primary-700' : 'text-ink-3 hover:text-ink-2'}`}
                >
                  {t(`agent.${f}`)}
                </button>
              ))}
              {(logUpdates.length > 0 || timeline.length > 0) && (
                <button
                  type="button"
                  onClick={clearAll}
                  className="rounded-chip border border-bad/30 bg-bad/10 px-2 py-0.5 text-xs font-medium text-bad-ink hover:bg-bad/15"
                >{t('agent.clear')}</button>
              )}
            </div>
          </div>
          {logLoadError && (
            <div role="alert" className="mb-2 flex flex-shrink-0 items-center gap-2 text-xs text-bad-ink">
              <span className="flex-1">{t('agent.activity_load_failed', { error: logLoadError })}</span>
              <button type="button" onClick={fetchActivityLog} className="underline">{t('common.retry')}</button>
            </div>
          )}
          {view === 'activity' ? (
            <RunTimeline
              items={items}
              store={liveStore}
              liveStepText={liveStepText}
              isLive={showLive}
              loading={!activityLoaded && items.length === 0}
              isRunning={isRunning}
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto rounded-control border border-line bg-sunken p-4 font-mono text-[11px]">
              {filteredLogs.length === 0 ? (
                <div className="py-8 text-center text-ink-3">
                  {isRunning ? t('agent.waiting_events') : t('agent.start_to_see')}
                </div>
              ) : (
                filteredLogs.map((update) => (<LogItem key={update.id} update={update} />))
              )}
            </div>
          )}
        </div>

        {/* Compact side column */}
        <div className="order-2 space-y-4">
          {/* Agent status + tokens */}
          <div className="card !p-4">
            <div className="mb-3 flex items-center gap-2">
              <Brain className="h-4 w-4 text-ink-2" aria-hidden="true" />
              <h3 className="section-title !text-base">{t('agent.agent_status')}</h3>
            </div>
            {isRunning && agentStatus ? (
              <dl className="space-y-1 text-sm">
                {agentStatus.config?.model && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-ink-2">{t('agent.model')}</dt>
                    <dd className="truncate font-mono text-xs font-medium text-ink" title={agentStatus.config.model}>{agentStatus.config.model}</dd>
                  </div>
                )}
                <div className="flex justify-between">
                  <dt className="text-ink-2">{t('agent.tasks_completed')}</dt>
                  <dd className="font-medium tabular-nums">{agentStatus.status?.cycle_count || 0}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-2">{t('agent.queue')}</dt>
                  <dd className="font-medium">{t('agent.pending', { count: agentStatus.status?.analysis_queue_size || 0 })}</dd>
                </div>
                <div className="flex items-start justify-between gap-2">
                  <dt className="text-ink-2">{t('agent.state')}</dt>
                  <dd className="rounded-chip bg-sunken px-2 py-0.5 text-right text-xs font-medium tabular-nums">
                    {formatAgentState(agentStatus.status)}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="py-2 text-sm text-ink-3">{t('agent.agent_not_running')}</p>
            )}
            {hasTokens && (
              <div className="mt-3 border-t border-line pt-3">
                <div className="mb-1.5 text-xs font-medium text-ink-2">{t('agent.token_usage')}</div>
                <div className="grid grid-cols-4 gap-1.5 text-center">
                  {tokenTiles.map((tile) => (
                    <div key={tile.label} className="rounded-chip border border-line bg-sunken px-1 py-1">
                      <div className="font-mono text-xs font-semibold tabular-nums text-ink">{tile.value.toLocaleString()}</div>
                      <div className="truncate text-[10px] text-ink-3">{tile.label}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Configuration */}
          <div className="card !p-4">
            <h3 className="section-title !text-base mb-3">{t('agent.configuration')}</h3>
            <div className="space-y-3">
              <div>
                <label htmlFor="agent-llm-provider" className="mb-1 block text-sm font-medium text-ink">{t('agent.llm_provider')}</label>
                <select id="agent-llm-provider" value={llmProvider} onChange={(e) => setLlmProvider(e.target.value)} className="select" disabled={isRunning}>
                  <option value="gemini">Google Gemini (SDK)</option>
                  <option value="google_vertex">Google Vertex AI</option>
                  <option value="openai">OpenAI</option>
                  <option value="azure_openai">Azure OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="mistral">Mistral AI</option>
                  <option value="groq">Groq</option>
                  <option value="xai">x.AI (Grok)</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="perplexity">Perplexity</option>
                  <option value="amazon_bedrock">Amazon Bedrock</option>
                  <option value="minimax">MiniMax</option>
                  <option value="vllm">vLLM (Local)</option>
                </select>
              </div>
              <div>
                <label htmlFor="agent-llm-model" className="mb-1 block text-sm font-medium text-ink">{t('agent.model')}</label>
                <input
                  id="agent-llm-model"
                  type="text" value={model} onChange={(e) => setModel(e.target.value)}
                  placeholder={providerModels[llmProvider] || ''}
                  className="input w-full placeholder:text-ink-3" disabled={isRunning}
                />
                <p className="mt-1 text-xs text-ink-3">
                  {model ? '' : providerModels[llmProvider] ? t('agent.using_default', { model: providerModels[llmProvider] }) : t('agent.leave_empty_default')}
                </p>
              </div>
            </div>
          </div>

          <ScheduledTasksPanel isRunning={isRunning} active={active} />
          <TriggerRulesPanel isRunning={isRunning} active={active} />

          {/* Data store (collapsed: it is reference, not activity) */}
          <details className="card !p-4 group">
            <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
              <Database className="h-4 w-4 text-ink-2" aria-hidden="true" />
              <h3 className="section-title !text-base flex-1">{t('agent.data_store')}</h3>
              {isRunning && agentStatus?.data_store_stats && (
                <span className="text-xs tabular-nums text-ink-3">{(agentStatus.data_store_stats.total_records || 0).toLocaleString()}</span>
              )}
            </summary>
            {isRunning && agentStatus?.data_store_stats ? (
              <div className="mt-3 space-y-3">
                <div className="flex justify-between text-sm">
                  <span className="text-ink-2">{t('agent.total_records')}</span>
                  <span className="font-medium tabular-nums">{(agentStatus.data_store_stats.total_records || 0).toLocaleString()}</span>
                </div>
                {agentStatus.data_store_stats.by_feature && (
                  <div className="max-h-60 space-y-1 overflow-y-auto border-t border-line pr-2 pt-3 text-xs text-ink-2">
                    {Object.entries(agentStatus.data_store_stats.by_feature).map(([feature, count]) => (
                      <div key={feature} className="flex justify-between">
                        <span className="capitalize">{feature}:</span>
                        <span className="tabular-nums">{count.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                )}
                {agentStatus.data_store_stats.time_range && (
                  <div className="border-t border-line pt-3 text-xs text-ink-2">
                    <div className="mb-1 font-medium">{t('agent.time_range')}</div>
                    {agentStatus.data_store_stats.time_range.min && (<div>{t('agent.time_from')} {formatFullDateTime(agentStatus.data_store_stats.time_range.min)}</div>)}
                    {agentStatus.data_store_stats.time_range.max && (<div>{t('agent.time_to')} {formatFullDateTime(agentStatus.data_store_stats.time_range.max)}</div>)}
                  </div>
                )}
              </div>
            ) : (
              <p className="mt-3 text-sm text-ink-3">{t('agent.no_data')}</p>
            )}
          </details>
        </div>
      </div>
    </div>
  )
}
