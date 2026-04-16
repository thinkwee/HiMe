import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Light as SyntaxHighlighter } from 'react-syntax-highlighter'
import python from 'react-syntax-highlighter/dist/esm/languages/hljs/python'
import { githubGist } from 'react-syntax-highlighter/dist/esm/styles/hljs'
import { api } from '../lib/api'
import { formatFullDateTime, parseBackendDate } from '../lib/utils'
import { useApp } from '../context/AppContext'
import { Play, Square, Brain, Activity, Database, Wifi, WifiOff, Calendar, X, Clock, Plus, Pause, Trash2, Zap, RotateCcw, Pencil, Check, Loader2, CheckCircle2, AlertCircle, Server, HardDrive, Cpu, ListChecks, Rocket } from 'lucide-react'

SyntaxHighlighter.registerLanguage('python', python)

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

function eventToMessage(ev) {
  const d = ev.data || ev
  const t = ev.type || d.type
  if (t === 'status_update') return null

  // Determine task type for badge
  const isQuick = d.task === 'quick_analysis' || t.startsWith('quick_analysis')
  const isChat = t === 'user_message' || t.startsWith('chat_')
  const taskType = isQuick ? 'quick' : isChat ? 'chat' : (d.goal ? 'scheduled' : 'analysis')

  let msg = null
  switch (t) {
    case 'user_message':
      msg = { text: `${d.sender || 'User'}: ${d.content}`, type: 'user_input' }
      break
    case 'chat_thinking':
      msg = { text: `💭 ${d.content}`, rawDelta: d.content, type: 'thinking', isStreaming: true }
      break
    case 'chat_content':
      msg = { text: `🤖 ${d.content}`, rawDelta: d.content, type: 'content', isStreaming: true }
      break
    case 'chat_tool_call':
    case 'analysis_tool_call':
    case 'quick_tool_call':
    case 'tool_call': {
      const toolName = d.tool || ''
      let callText
      if (toolName === 'sql') {
        const query = d.arguments?.query || ''
        callText = query
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
      break
    }
    case 'chat_tool_result':
    case 'analysis_tool_result':
    case 'quick_tool_result':
    case 'tool_result': {
      const toolName = d.tool || ''
      if (d.success) {
        const r = d.result || {}
        if (toolName === 'sql' && r.columns && r.rows) {
          const meta = `${r.row_count ?? r.rows.length} rows${r.truncated ? ' (truncated)' : ''}`
          msg = { text: meta, type: 'tool_result', toolName, toolSuccess: true, sqlData: { columns: r.columns, rows: r.rows } }
        } else {
          let content = r.output ?? r.data ?? r.message ?? ''
          if (typeof content === 'object') content = JSON.stringify(content, null, 2)
          msg = { text: String(content), type: 'tool_result', toolName, toolSuccess: true }
        }
      } else {
        msg = { text: d.result?.error || 'Unknown error', type: 'tool_result', toolName, toolSuccess: false }
      }
      break
    }
    case 'agent_thinking':
      msg = d.content?.trim() ? { text: `💭 ${d.content}`, rawDelta: d.content, type: 'thinking', isStreaming: true } : null
      break
    case 'content':
      msg = d.content?.trim() ? { text: `🤖 Assistant: ${d.content}`, rawDelta: d.content, type: 'content', isStreaming: true } : null
      break
    case 'error':
      msg = { text: `❌ Error: ${d.error || ''}. Agent will auto-restart with backoff.`, type: 'error' }
      break
    case 'agent_error':
      msg = { text: `🔄 Agent restarted after error: ${d.error || 'unknown'}`, type: 'warning' }
      break
    case 'agent_started':
      msg = { text: '🚀 Agent started', type: 'system' }
      break
    case 'startup_progress':
      msg = { text: `⏳ [${d.step}/${d.total}] ${d.label}`, type: 'system' }
      break
    case 'startup_error':
      msg = { text: `❌ Startup failed: ${d.error || 'unknown'}`, type: 'error' }
      break
    case 'agent_stopped':
      msg = { text: `🛑 Agent stopped: ${d.reason || d.timestamp || ''}`, type: 'system' }
      break
    case 'cycle_start':
      msg = { text: `🔄 Task #${d.cycle || ''} started${d.goal ? ` — ${d.goal.slice(0, 80)}` : ''}`, type: 'system' }
      break
    case 'cycle_end':
      msg = { text: `✅ Task #${d.cycle || ''} completed`, type: 'system' }
      break
    case 'report_pushed':
      msg = { text: `📊 Report pushed (ID: ${d.report_id || '?'})`, type: 'system' }
      break
    case 'forced_sleep':
      msg = { text: `⚠️ Analysis cycle ended without report — ${d.reason || 'max turns reached'}. Agent will retry next cycle.`, type: 'warning' }
      break
    case 'monitor_connected':
      msg = { text: '📡 Monitor connected to agent', type: 'system' }
      break
    case 'token_truncated':
      msg = { text: `⚠️ Response truncated at ${d.completion_tokens}/${d.max_tokens} tokens (output limit reached)`, type: 'warning' }
      break
    case 'quick_analysis_start':
      msg = { text: '🐱 Quick analysis started (iOS long-press)', type: 'system' }
      break
    case 'quick_analysis_complete':
      msg = { text: `🐱 Quick analysis complete → ${d.state || 'neutral'}`, type: 'system' }
      break
  }

  if (msg) return { ...msg, taskType }
  return null
}

function formatTokenUsage(tu) {
  if (!tu) return null
  const prompt = tu.prompt_tokens
  const completion = tu.completion_tokens ?? tu.response_tokens
  if (prompt == null && completion == null) return null

  const in_ = prompt ?? '-'
  const out = completion ?? '-'
  const parts = []
  if (tu.thoughts_tokens != null && (tu.response_tokens != null || tu.completion_tokens != null)) {
    parts.push(`thinking ${tu.thoughts_tokens}`)
    parts.push(`response ${tu.response_tokens ?? tu.completion_tokens}`)
  } else if (tu.response_tokens != null || tu.completion_tokens != null) {
    parts.push(`response ${tu.response_tokens ?? tu.completion_tokens}`)
  }
  if (tu.cache_read_tokens != null && tu.cache_read_tokens > 0) {
    parts.push(`cache hit ${tu.cache_read_tokens}`)
  }
  if (tu.cache_creation_tokens != null && tu.cache_creation_tokens > 0) {
    parts.push(`cache write ${tu.cache_creation_tokens}`)
  }
  const detail = parts.length ? ` (${parts.join(', ')})` : ''
  return `📊 in ${in_} / out ${out}${detail}`
}

const TASK_TYPE_BADGE = {
  analysis: { labelKey: 'agent.badge_analysis', cls: 'bg-emerald-100 text-emerald-700' },
  chat: { labelKey: 'agent.badge_chat', cls: 'bg-indigo-100 text-indigo-700' },
  scheduled: { labelKey: 'agent.badge_scheduled', cls: 'bg-amber-100 text-amber-700' },
  quick: { labelKey: 'agent.badge_quick', cls: 'bg-pink-100 text-pink-700' },
}

// Per-tool theme colours — avoids clashing with task-type badge colours
const TOOL_THEME = {
  sql:            { bg: 'bg-cyan-50/60',   text: 'text-cyan-700',    header: 'bg-cyan-100/60 text-cyan-800',    border: 'border-cyan-200/50',    labelKey: 'agent.tool_sql',         icon: '🔍' },
  code:           { bg: 'bg-violet-50/60',  text: 'text-violet-700',  header: 'bg-violet-100/60 text-violet-800', border: 'border-violet-200/50', labelKey: 'agent.tool_code',        icon: '⚡' },
  push_report:    { bg: 'bg-blue-50/60',    text: 'text-blue-700',    header: 'bg-blue-100/60 text-blue-800',    border: 'border-blue-200/50',    labelKey: 'agent.tool_push_report', icon: '📊' },
  update_md:      { bg: 'bg-slate-50/60',   text: 'text-slate-600',   header: 'bg-slate-100/60 text-slate-700',  border: 'border-slate-200/50',   labelKey: 'agent.tool_update_md',   icon: '📝' },
  reply_user:     { bg: 'bg-sky-50/60',     text: 'text-sky-700',     header: 'bg-sky-100/60 text-sky-800',      border: 'border-sky-200/50',     labelKey: 'agent.tool_reply_user',  icon: '✉️' },
  finish_chat:    { bg: 'bg-stone-50/60',   text: 'text-stone-600',   header: 'bg-stone-100/60 text-stone-700',  border: 'border-stone-200/50',   labelKey: 'agent.tool_finish_chat', icon: '💬' },
  sleep:          { bg: 'bg-stone-50/60',   text: 'text-stone-600',   header: 'bg-stone-100/60 text-stone-700',  border: 'border-stone-200/50',   labelKey: 'agent.tool_sleep',       icon: '💤' },
  create_page:    { bg: 'bg-rose-50/60',    text: 'text-rose-700',    header: 'bg-rose-100/60 text-rose-800',    border: 'border-rose-200/50',    labelKey: 'agent.tool_create_page', icon: '🧩' },
  read_skill:     { bg: 'bg-amber-50/60',   text: 'text-amber-700',   header: 'bg-amber-100/60 text-amber-800',  border: 'border-amber-200/50',   labelKey: 'agent.tool_read_skill',  icon: '📖' },
}
const DEFAULT_TOOL_THEME = { bg: 'bg-gray-50/60', text: 'text-gray-600', header: 'bg-gray-100/60 text-gray-700', border: 'border-gray-200/50', labelKey: 'agent.tool_generic', icon: '🔧' }

function ToolResultBlock({ text, toolName, sqlData, theme }) {
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
              <tr key={ri} className={ri % 2 === 0 ? 'bg-white/50' : theme.bg}>
                {row.map((val, ci) => (
                  <td key={ci} className={`px-2 py-0.5 border ${theme.border} text-gray-700 max-w-xs`}>
                    {val == null ? <span className="text-gray-300">null</span> : String(val).length > 200 ? String(val).slice(0, 200) + '…' : String(val)}
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
    <div className={`mt-1 ${theme.bg} rounded px-2.5 py-1.5 border ${theme.border} font-mono text-[10px] ${theme.text} whitespace-pre-wrap max-h-40 overflow-y-auto`}>
      {text}
    </div>
  )
}

// Syntax-highlighted Python code block
const codeHighlightStyle = { ...githubGist, hljs: { ...githubGist.hljs, background: 'transparent', padding: 0 } }
function PythonBlock({ code, theme, maxH = 'max-h-32' }) {
  return (
    <div className={`mt-1 ${theme.bg} rounded px-2.5 py-1.5 border ${theme.border} ${maxH} overflow-y-auto`}>
      <SyntaxHighlighter language="python" style={codeHighlightStyle} customStyle={{ fontSize: '10px', margin: 0, background: 'transparent' }}>
        {code}
      </SyntaxHighlighter>
    </div>
  )
}

function LogItem({ update }) {
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

  // ── Tool call ──────────────────────────────────────────────────────────
  if (type === 'tool_call' && theme) {
    return (
      <div className="mb-1.5 pb-1 border-b border-gray-100/50 last:border-0">
        <span className="text-gray-400 select-none mr-1.5">[{update.time}]</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded mr-1.5 ${badge.cls}`}>{t(badge.labelKey)}</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0.5 rounded mr-1 ${theme.header}`}>{theme.icon} {t(theme.labelKey)}</span>
        {/* Short args inline, long args in a block; code tool gets syntax highlighting */}
        {text && text.length < 100 && !text.includes('\n') ? (
          <span className={`${theme.text} font-mono text-[10px]`}> {text}</span>
        ) : text && toolName === 'code' ? (
          <PythonBlock code={text} theme={theme} />
        ) : text ? (
          <div className={`mt-1 ${theme.bg} rounded px-2.5 py-1.5 border ${theme.border} font-mono text-[10px] ${theme.text} whitespace-pre-wrap max-h-32 overflow-y-auto`}>
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
      <div className="mb-1.5 pb-1 border-b border-gray-100/50 last:border-0">
        <span className="text-gray-400 select-none mr-1.5">[{update.time}]</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded mr-1.5 ${badge.cls}`}>{t(badge.labelKey)}</span>
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0.5 rounded mr-1 ${theme.header}`}>{theme.icon} {t(theme.labelKey)}</span>
        <span className={toolSuccess ? theme.text : 'text-red-600 font-medium'}>
          {icon}{hasBlock ? '' : ` ${text}`}
        </span>
        {toolSuccess && <ToolResultBlock text={text} toolName={toolName} sqlData={sqlData} theme={theme} />}
      </div>
    )
  }

  // ── Non-tool event types ───────────────────────────────────────────────
  let textColor = 'text-gray-700'
  let bgColor = ''

  if (type === 'thinking') {
    textColor = 'text-blue-600 italic'
  } else if (type === 'content') {
    textColor = 'text-indigo-800 font-medium'
    bgColor = 'bg-indigo-50/50 rounded px-1'
  } else if (type === 'user_input') {
    textColor = 'text-amber-700 font-bold'
    bgColor = 'bg-amber-50 rounded px-1'
  } else if (type === 'error') {
    textColor = 'text-red-600 font-medium'
  } else if (type === 'warning') {
    textColor = 'text-yellow-700 font-medium'
  } else if (type === 'system') {
    textColor = 'text-gray-500 font-medium'
  } else if (type === 'token_usage') {
    textColor = 'text-gray-600 font-mono text-xs'
  }

  const tokenLine = (type === 'content' || type === 'token_usage') && tokenUsage ? formatTokenUsage(tokenUsage) : (type === 'token_usage' ? text : null)

  return (
    <div className={`mb-1.5 pb-1 border-b border-gray-100/50 last:border-0 whitespace-pre-wrap ${bgColor}`}>
      <span className="text-gray-400 select-none mr-1.5">[{update.time}]</span>
      {type !== 'system' && type !== 'token_usage' && (
        <span className={`inline-block text-[9px] font-bold px-1.5 py-0 rounded mr-1.5 ${badge.cls}`}>
          {t(badge.labelKey)}
        </span>
      )}
      <span className={textColor}>{type === 'token_usage' ? (tokenLine || text) : text}</span>
      {type !== 'token_usage' && tokenLine && (
        <div className="mt-1 text-xs text-gray-500 font-mono">{tokenLine}</div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Scheduled Tasks Panel
// ---------------------------------------------------------------------------
function ScheduledTasksPanel({ isRunning }) {
  const { t } = useTranslation()
  const [tasks, setTasks] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [newCron, setNewCron] = useState('0 8 * * *')
  const [newGoal, setNewGoal] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editCron, setEditCron] = useState('')
  const [editGoal, setEditGoal] = useState('')

  const fetchTasks = useCallback(async () => {
    const res = await api.getScheduledTasks()
    if (res.success) setTasks(res.tasks || [])
  }, [])

  useEffect(() => {
    fetchTasks()
    const interval = setInterval(fetchTasks, 15000)
    return () => clearInterval(interval)
  }, [fetchTasks])

  const handleCreate = async () => {
    if (!newCron.trim() || !newGoal.trim()) return
    const res = await api.createScheduledTask(newCron.trim(), newGoal.trim())
    if (res.success) {
      setNewCron('0 8 * * *')
      setNewGoal('')
      setShowAdd(false)
      fetchTasks()
    } else {
      alert(res.error || t('agent.failed_create_task'))
    }
  }

  const handleToggle = async (task) => {
    const newStatus = task.status === 'active' ? 'paused' : 'active'
    await api.updateScheduledTask(task.id, { status: newStatus })
    fetchTasks()
  }

  const handleDelete = async (task) => {
    await api.updateScheduledTask(task.id, { status: 'deleted' })
    fetchTasks()
  }

  const handleTrigger = async (task) => {
    if (!isRunning) { alert(t('agent.start_agent_first')); return }
    await api.triggerAnalysis(task.prompt_goal)
  }

  const handleEdit = (task) => {
    setEditingId(task.id)
    setEditCron(task.cron_expr)
    setEditGoal(task.prompt_goal)
  }

  const handleSaveEdit = async () => {
    if (!editCron.trim() || !editGoal.trim()) return
    await api.updateScheduledTask(editingId, { cron_expr: editCron.trim(), prompt_goal: editGoal.trim() })
    setEditingId(null)
    fetchTasks()
  }

  const cronHuman = (expr) => {
    const parts = expr.split(' ')
    if (parts.length !== 5) return expr
    const [min, hour, , , dow] = parts
    const dowMap = { '0': 'Sun', '1': 'Mon', '2': 'Tue', '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat', '*': 'daily' }
    const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
    return `${dowMap[dow] || dow} ${time}`
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
          <Clock className="w-5 h-5 text-gray-600" />
          {t('agent.scheduled_tasks')}
        </h3>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="text-xs flex items-center gap-1 text-primary-600 hover:text-primary-800"
        >
          <Plus className="w-3 h-3" /> {t('common.add')}
        </button>
      </div>

      {showAdd && (
        <div className="mb-3 p-3 bg-gray-50 rounded border border-gray-200 space-y-2">
          <input
            type="text"
            value={newCron}
            onChange={(e) => setNewCron(e.target.value)}
            placeholder={t('agent.cron_placeholder')}
            className="input w-full text-sm font-mono"
          />
          <textarea
            value={newGoal}
            onChange={(e) => setNewGoal(e.target.value)}
            placeholder={t('agent.analysis_goal_placeholder')}
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
        <p className="text-sm text-gray-400 text-center py-4">{t('agent.no_scheduled_tasks')}</p>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            editingId === task.id ? (
              <div key={task.id} className="p-2.5 rounded border border-primary-200 bg-primary-50/30 text-sm space-y-2">
                <input type="text" value={editCron} onChange={(e) => setEditCron(e.target.value)} className="input w-full text-sm font-mono" placeholder={t('agent.cron_expression')} />
                <textarea value={editGoal} onChange={(e) => setEditGoal(e.target.value)} className="input w-full text-sm" rows={2} placeholder={t('agent.analysis_goal_placeholder')} />
                <div className="flex gap-1">
                  <button onClick={handleSaveEdit} className="p-1 hover:bg-green-50 rounded" title={t('common.save')}><Check className="w-3.5 h-3.5 text-green-600" /></button>
                  <button onClick={() => setEditingId(null)} className="p-1 hover:bg-gray-100 rounded" title={t('common.cancel')}><X className="w-3.5 h-3.5 text-gray-400" /></button>
                </div>
              </div>
            ) : (
              <div key={task.id} className={`p-2.5 rounded border text-sm ${task.status === 'active' ? 'bg-white border-gray-200' : 'bg-gray-50 border-gray-100 opacity-60'}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded">{cronHuman(task.cron_expr)}</span>
                      <span className={`text-[10px] font-bold uppercase px-1.5 rounded ${task.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                        {task.status}
                      </span>
                    </div>
                    <p className="text-gray-700 text-xs truncate">{task.prompt_goal}</p>
                  </div>
                  <div className="flex gap-1 flex-shrink-0">
                    {isRunning && (
                      <button onClick={() => handleTrigger(task)} className="p-1 hover:bg-blue-50 rounded" title={t('agent.run_now')}>
                        <Play className="w-3.5 h-3.5 text-blue-500" />
                      </button>
                    )}
                    <button onClick={() => handleEdit(task)} className="p-1 hover:bg-blue-50 rounded" title={t('common.edit')}>
                      <Pencil className="w-3.5 h-3.5 text-blue-400" />
                    </button>
                    <button onClick={() => handleToggle(task)} className="p-1 hover:bg-yellow-50 rounded" title={task.status === 'active' ? t('agent.pause') : t('agent.resume')}>
                      {task.status === 'active' ? <Pause className="w-3.5 h-3.5 text-yellow-500" /> : <RotateCcw className="w-3.5 h-3.5 text-green-500" />}
                    </button>
                    <button onClick={() => handleDelete(task)} className="p-1 hover:bg-red-50 rounded" title={t('common.delete')}>
                      <Trash2 className="w-3.5 h-3.5 text-red-400" />
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
const TRIGGER_CONDITIONS = [
  ['gt', '> greater than'], ['lt', '< less than'],
  ['gte', '\u2265 greater or equal'], ['lte', '\u2264 less or equal'],
  ['avg_gt', 'avg > threshold'], ['avg_lt', 'avg < threshold'],
  ['spike', 'spike (\u03c3)'], ['drop', 'drop (\u03c3)'],
  ['delta_gt', 'delta > threshold'], ['absent', 'data absent'],
]

const CONDITION_LABELS = { gt: '>', lt: '<', gte: '\u2265', lte: '\u2264', avg_gt: 'avg >', avg_lt: 'avg <', spike: 'spike', drop: 'drop', delta_gt: '\u0394 >', absent: 'absent' }

function ConditionSelect({ value, onChange, className = '' }) {
  return (
    <select value={value} onChange={onChange} className={`input text-sm ${className}`}>
      {TRIGGER_CONDITIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
    </select>
  )
}

function TriggerRulesPanel({ isRunning }) {
  const { t } = useTranslation()
  const [rules, setRules] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [editingId, setEditingId] = useState(null)

  const emptyRule = { name: '', feature_type: '', condition: 'gt', threshold: '', window_minutes: 60, cooldown_minutes: 30, prompt_goal: '' }
  const [newRule, setNewRule] = useState(emptyRule)
  const [editRule, setEditRule] = useState({})

  const fetchRules = useCallback(async () => {
    const res = await api.getTriggerRules()
    if (res.success) setRules(res.rules || [])
  }, [])

  useEffect(() => {
    fetchRules()
    const interval = setInterval(fetchRules, 15000)
    return () => clearInterval(interval)
  }, [fetchRules])

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
      alert(res.error || t('agent.failed_create_rule'))
    }
  }

  const handleToggle = async (rule) => {
    const newStatus = rule.status === 'active' ? 'paused' : 'active'
    await api.updateTriggerRule(rule.id, { status: newStatus })
    fetchRules()
  }

  const handleDelete = async (rule) => {
    await api.updateTriggerRule(rule.id, { status: 'deleted' })
    fetchRules()
  }

  const handleTrigger = async (rule) => {
    if (!isRunning) { alert(t('agent.start_agent_first')); return }
    await api.triggerAnalysis(rule.prompt_goal)
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
    await api.updateTriggerRule(editingId, {
      ...editRule,
      threshold: parseFloat(editRule.threshold) || 0,
      window_minutes: parseInt(editRule.window_minutes) || 60,
      cooldown_minutes: parseInt(editRule.cooldown_minutes) || 30,
    })
    setEditingId(null)
    fetchRules()
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
          <Zap className="w-5 h-5 text-amber-500" />
          {t('agent.trigger_rules')}
        </h3>
        <button onClick={() => setShowAdd(!showAdd)} className="text-xs flex items-center gap-1 text-primary-600 hover:text-primary-800">
          <Plus className="w-3 h-3" /> {t('common.add')}
        </button>
      </div>

      {showAdd && (
        <div className="mb-3 p-3 bg-gray-50 rounded border border-gray-200 space-y-2">
          <input type="text" value={newRule.name} onChange={(e) => setNewRule({ ...newRule, name: e.target.value })} placeholder={t('agent.rule_name')} className="input w-full text-sm" />
          <div className="grid grid-cols-2 gap-2">
            <input type="text" value={newRule.feature_type} onChange={(e) => setNewRule({ ...newRule, feature_type: e.target.value })} placeholder={t('agent.feature_placeholder')} className="input text-sm" />
            <ConditionSelect value={newRule.condition} onChange={(e) => setNewRule({ ...newRule, condition: e.target.value })} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <input type="number" value={newRule.threshold} onChange={(e) => setNewRule({ ...newRule, threshold: e.target.value })} placeholder={t('agent.threshold')} className="input text-sm" />
            <input type="number" value={newRule.window_minutes} onChange={(e) => setNewRule({ ...newRule, window_minutes: e.target.value })} placeholder={t('agent.window_min')} className="input text-sm" />
            <input type="number" value={newRule.cooldown_minutes} onChange={(e) => setNewRule({ ...newRule, cooldown_minutes: e.target.value })} placeholder={t('agent.cooldown_min')} className="input text-sm" />
          </div>
          <textarea value={newRule.prompt_goal} onChange={(e) => setNewRule({ ...newRule, prompt_goal: e.target.value })} placeholder={t('agent.triggered_when_placeholder')} className="input w-full text-sm" rows={2} />
          <div className="flex gap-2">
            <button onClick={handleCreate} className="btn btn-primary text-xs px-3 py-1">{t('common.create')}</button>
            <button onClick={() => setShowAdd(false)} className="btn text-xs px-3 py-1">{t('common.cancel')}</button>
          </div>
        </div>
      )}

      {rules.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">{t('agent.no_trigger_rules')}</p>
      ) : (
        <div className="space-y-2">
          {rules.map((rule) => (
            editingId === rule.id ? (
              <div key={rule.id} className="p-2.5 rounded border border-primary-200 bg-primary-50/30 text-sm space-y-2">
                <input type="text" value={editRule.name} onChange={(e) => setEditRule({ ...editRule, name: e.target.value })} className="input w-full text-sm" placeholder={t('agent.rule_name')} />
                <div className="grid grid-cols-2 gap-2">
                  <input type="text" value={editRule.feature_type} onChange={(e) => setEditRule({ ...editRule, feature_type: e.target.value })} className="input text-sm" placeholder={t('agent.feature_type')} />
                  <ConditionSelect value={editRule.condition} onChange={(e) => setEditRule({ ...editRule, condition: e.target.value })} />
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <input type="number" value={editRule.threshold} onChange={(e) => setEditRule({ ...editRule, threshold: e.target.value })} placeholder={t('agent.threshold')} className="input text-sm" />
                  <input type="number" value={editRule.window_minutes} onChange={(e) => setEditRule({ ...editRule, window_minutes: e.target.value })} placeholder={t('agent.window_min')} className="input text-sm" />
                  <input type="number" value={editRule.cooldown_minutes} onChange={(e) => setEditRule({ ...editRule, cooldown_minutes: e.target.value })} placeholder={t('agent.cooldown_min')} className="input text-sm" />
                </div>
                <textarea value={editRule.prompt_goal} onChange={(e) => setEditRule({ ...editRule, prompt_goal: e.target.value })} className="input w-full text-sm" rows={2} placeholder={t('agent.analysis_goal_placeholder')} />
                <div className="flex gap-1">
                  <button onClick={handleSaveEdit} className="p-1 hover:bg-green-50 rounded" title={t('common.save')}><Check className="w-3.5 h-3.5 text-green-600" /></button>
                  <button onClick={() => setEditingId(null)} className="p-1 hover:bg-gray-100 rounded" title={t('common.cancel')}><X className="w-3.5 h-3.5 text-gray-400" /></button>
                </div>
              </div>
            ) : (
              <div key={rule.id} className={`p-2.5 rounded border text-sm ${rule.status === 'active' ? 'bg-white border-gray-200' : 'bg-gray-50 border-gray-100 opacity-60'}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-xs text-gray-900">{rule.name}</span>
                      <span className={`text-[10px] font-bold uppercase px-1.5 rounded ${rule.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                        {rule.status}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 font-mono mb-0.5">
                      {rule.feature_type} {CONDITION_LABELS[rule.condition] || rule.condition} {rule.threshold}
                      <span className="text-gray-400 ml-2">{rule.window_minutes}m / {rule.cooldown_minutes}m cd</span>
                    </div>
                    <p className="text-gray-700 text-xs truncate">{rule.prompt_goal}</p>
                    {rule.trigger_count > 0 && (
                      <div className="text-[10px] text-gray-400 mt-0.5">{t('agent.triggered_times', { count: rule.trigger_count })}</div>
                    )}
                  </div>
                  <div className="flex gap-1 flex-shrink-0">
                    {isRunning && (
                      <button onClick={() => handleTrigger(rule)} className="p-1 hover:bg-blue-50 rounded" title={t('agent.run_now')}>
                        <Play className="w-3.5 h-3.5 text-blue-500" />
                      </button>
                    )}
                    <button onClick={() => handleEdit(rule)} className="p-1 hover:bg-blue-50 rounded" title={t('common.edit')}>
                      <Pencil className="w-3.5 h-3.5 text-blue-400" />
                    </button>
                    <button onClick={() => handleToggle(rule)} className="p-1 hover:bg-yellow-50 rounded" title={rule.status === 'active' ? t('agent.pause') : t('agent.resume')}>
                      {rule.status === 'active' ? <Pause className="w-3.5 h-3.5 text-yellow-500" /> : <RotateCcw className="w-3.5 h-3.5 text-green-500" />}
                    </button>
                    <button onClick={() => handleDelete(rule)} className="p-1 hover:bg-red-50 rounded" title={t('common.delete')}>
                      <Trash2 className="w-3.5 h-3.5 text-red-400" />
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
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className={`px-6 py-4 ${error ? 'bg-red-50' : done ? 'bg-green-50' : 'bg-indigo-50'}`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              {error ? (
                <AlertCircle className="w-6 h-6 text-red-500" />
              ) : done ? (
                <CheckCircle2 className="w-6 h-6 text-green-500" />
              ) : (
                <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
              )}
              <h3 className="text-lg font-semibold text-gray-900">
                {error ? t('agent.startup_failed') : done ? t('agent.agent_ready') : t('agent.starting_agent')}
              </h3>
            </div>
            {(done || error) && (
              <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
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
              <div key={key} className={`flex items-center space-x-3 py-2 px-3 rounded-lg transition-all duration-300 ${
                active ? 'bg-indigo-50' : completed ? 'bg-gray-50' : ''
              }`}>
                <div className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition-all duration-300 ${
                  completed ? 'bg-green-100' : active ? 'bg-indigo-100' : 'bg-gray-100'
                }`}>
                  {completed ? (
                    <Check className="w-4 h-4 text-green-600" />
                  ) : active ? (
                    <Loader2 className="w-4 h-4 text-indigo-600 animate-spin" />
                  ) : (
                    <Icon className={`w-4 h-4 ${pending ? 'text-gray-300' : 'text-gray-400'}`} />
                  )}
                </div>
                <span className={`text-sm transition-colors duration-300 ${
                  completed ? 'text-gray-500' : active ? 'text-indigo-700 font-medium' : 'text-gray-400'
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
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <p className="text-sm text-red-700 font-mono break-all">{error}</p>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-6 pb-5">
          {done ? (
            <button onClick={onClose}
              className="w-full py-2.5 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium transition-colors">
              {t('common.done')}
            </button>
          ) : error ? (
            <button onClick={onClose}
              className="w-full py-2.5 bg-gray-600 hover:bg-gray-700 text-white rounded-lg font-medium transition-colors">
              {t('common.dismiss')}
            </button>
          ) : (
            <div className="w-full bg-gray-200 rounded-full h-1.5 overflow-hidden">
              <div className="bg-indigo-500 h-full rounded-full transition-all duration-500 ease-out"
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
export default function AutonomousAgentMonitor() {
  const { t } = useTranslation()
  // State
  const [agentStatus, setAgentStatus] = useState(null)
  const [isRunning, setIsRunning] = useState(false)
  const [logUpdates, setLogUpdates] = useState([])
  const [logFilter, setLogFilter] = useState('all') // 'all' | 'analysis' | 'chat'
  const [llmProvider, setLlmProvider] = useState('gemini')
  const [model, setModel] = useState('')
  const [defaultModel, setDefaultModel] = useState('')
  const [providerModels, setProviderModels] = useState({})
  const [wsConnected, setWsConnected] = useState(false)
  const [reports, setReports] = useState([])
  const [selectedReport, setSelectedReport] = useState(null)
  const [cumulativeTokens, setCumulativeTokens] = useState({ prompt: 0, thoughts: 0, response: 0, cacheRead: 0, cacheCreation: 0 })
  const [localDurations, setLocalDurations] = useState({ analysis: 0, chat: 0 })
  const [liveStream, setLiveStream] = useState({ thinking: '', content: '' })

  const [wsReconnecting, setWsReconnecting] = useState(false)
  const [startupModal, setStartupModal] = useState(null) // null | { step, error }

  // Refs
  const wsRef = useRef(null)
  const wsStateRef = useRef('disconnected') // 'disconnected' | 'connecting' | 'connected'
  const wsReconnectTimerRef = useRef(null)
  const wsReconnectAttemptsRef = useRef(0)
  const configSyncedRef = useRef(false) // track whether we've synced config from running agent
  const connectMonitorRef = useRef(null) // stable ref for reconnect to call
  const isRunningRef = useRef(false) // track isRunning for WS onclose to check
  const analysisStateKeyRef = useRef(null)
  const chatStateKeyRef = useRef(null)
  const analysisLocalStartRef = useRef(null)
  const chatLocalStartRef = useRef(null)
  const streamBufferRef = useRef({ content: '', thinking: '' })

  const stripToolCallXml = (text) => (text || '').replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '').trim()

  // Flush accumulated streaming content into a single log entry
  const flushStreamBuffer = useCallback(() => {
    const buf = streamBufferRef.current
    const toAdd = []
    if (buf.thinking) {
      toAdd.push({ text: `💭 ${buf.thinking}`, type: 'thinking', taskType: buf.taskType || 'analysis' })
    }
    const contentStripped = stripToolCallXml(buf.content)
    if (contentStripped) {
      toAdd.push({ text: `🤖 Assistant: ${contentStripped}`, type: 'content', taskType: buf.taskType || 'analysis' })
    }
    if (toAdd.length > 0) {
      const time = new Date().toLocaleTimeString()
      setLogUpdates((prev) => [...toAdd.map((m) => ({ time, message: m })), ...prev.slice(0, 499)])
    }
    buf.content = ''
    buf.thinking = ''
    buf.taskType = 'analysis'
    setLiveStream({ thinking: '', content: '' })
  }, [])

  const addStatusUpdate = useCallback((msgObj) => {
    const { taskType, ...message } = msgObj
    const buf = streamBufferRef.current

    if (message.isStreaming) {
      const delta = message.rawDelta ?? message.text?.replace(/^💭 |^🤖 Assistant: /, '') ?? ''
      buf.taskType = taskType || 'analysis'
      if (message.type === 'thinking') {
        if (buf.content) { flushStreamBuffer(); buf.content = '' }
        buf.thinking += delta
      } else if (message.type === 'content') {
        if (buf.thinking) { flushStreamBuffer(); buf.thinking = '' }
        buf.content += delta
      }
      return
    }

    flushStreamBuffer()
    const update = { time: new Date().toLocaleTimeString(), message: { ...message, taskType: taskType || 'analysis' } }
    setLogUpdates((prev) => [update, ...prev.slice(0, 499)])
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

  // Timer for smooth state duration
  useEffect(() => {
    const timer = setInterval(() => {
      const now = Date.now()
      setLocalDurations({
        analysis: analysisLocalStartRef.current ? Math.floor((now - analysisLocalStartRef.current) / 1000) : 0,
        chat: chatLocalStartRef.current ? Math.floor((now - chatLocalStartRef.current) / 1000) : 0,
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  // Timer to sync streaming buffer to live preview state (real-time display)
  useEffect(() => {
    const timer = setInterval(() => {
      const buf = streamBufferRef.current
      setLiveStream({ thinking: buf.thinking || '', content: buf.content || '' })
    }, 150)
    return () => clearInterval(timer)
  }, [])

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

  const updateTimingRefs = (status) => {
    const newAnalysisState = status?.analysis_state
    if (newAnalysisState !== analysisStateKeyRef.current) {
      analysisStateKeyRef.current = newAnalysisState
      analysisLocalStartRef.current = Date.now() - (status?.analysis_state_duration * 1000 || 0)
    }
    const newChatState = status?.chat_state
    if (newChatState !== chatStateKeyRef.current) {
      chatStateKeyRef.current = newChatState
      chatLocalStartRef.current = Date.now() - (status?.chat_state_duration * 1000 || 0)
    }
  }

  // API Calls
  const checkAgentStatus = useCallback(async () => {
    try {
      const result = await api.getAgentStatus('LiveUser')
      if (result.success && result.running) {
        setAgentStatus(result)
        setIsRunning(true)
        updateTimingRefs(result.status)
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
      } else {
        setAgentStatus(null)
        setIsRunning(false)
        configSyncedRef.current = false
      }
    } catch (error) {
      console.error('Failed to get agent status:', error)
    }
  }, [])

  const fetchReports = useCallback(async () => {
    try {
      const result = await api.queryAgentMemory('reports')
      if (result.success && result.data) setReports(result.data)
    } catch (error) { /* Silent */ }
  }, [])

  const fetchActivityLog = useCallback(async () => {
    try {
      const result = await api.getAgentActivity(500)
      if (result.success && result.events?.length) {
        const items = []
        result.events.forEach((ev) => {
          const msg = eventToMessage(ev)
          if (!msg) return
          if (msg.isStreaming) return
          items.push({
            time: ev.created_at ? parseBackendDate(ev.created_at).toLocaleTimeString() : '',
            message: msg,
          })
        })
        setLogUpdates(items.reverse())
      }
    } catch (e) { /* Silent */ }
  }, [])

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
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null }

    const websocket = api.connectAgentMonitor()

    websocket.onopen = () => {
      wsStateRef.current = 'connected'
      wsReconnectAttemptsRef.current = 0
      setWsConnected(true)
      setWsReconnecting(false)
      addStatusUpdate({ taskType: 'analysis', text: `📡 ${t('agent.monitor_connected')}`, type: 'system' })
    }

    websocket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        if (data.type === 'status_update') {
          updateTimingRefs(data.status)
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
        } else if (data.type === 'token_usage') {
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
          const buf = streamBufferRef.current
          const time = new Date().toLocaleTimeString()
          setLiveStream({ thinking: '', content: '' })
          const toPrepend = []
          if (buf.thinking) {
            toPrepend.push({ time, message: { text: `💭 ${buf.thinking}`, type: 'thinking', taskType: buf.taskType || 'analysis' } })
          }
          const contentStripped = stripToolCallXml(buf.content)
          if (contentStripped) {
            toPrepend.push({ time, message: { text: `🤖 Assistant: ${contentStripped}`, type: 'content', taskType: buf.taskType || 'analysis' } })
          }
          buf.thinking = ''
          buf.content = ''
          setLogUpdates((prev) => {
            const withFlushed = [...toPrepend, ...prev]
            let idx = -1
            for (let i = withFlushed.length - 1; i >= 0; i--) {
              if (withFlushed[i].message?.type === 'content') { idx = i; break }
            }
            if (idx >= 0) {
              const next = [...withFlushed]
              next[idx] = { ...next[idx], message: { ...next[idx].message, tokenUsage } }
              return next.slice(0, 500)
            }
            const tokenLine = formatTokenUsage(tokenUsage)
            if (tokenLine) {
              return [{ time, message: { text: tokenLine, type: 'token_usage', tokenUsage, taskType: isChat ? 'chat' : 'analysis' } }, ...withFlushed.slice(0, 199)]
            }
            return withFlushed.slice(0, 500)
          })
        } else {
          if (data.type === 'startup_progress') {
            setStartupModal({ step: data.step, error: null })
          } else if (data.type === 'agent_started') {
            // Mark startup complete (step 8 = past the last step)
            setStartupModal((prev) => prev ? { step: 8, error: null } : null)
            streamBufferRef.current = { content: '', thinking: '', taskType: 'analysis' }
            setLiveStream({ thinking: '', content: '' })
          } else if (data.type === 'startup_error') {
            setStartupModal((prev) => ({ step: prev?.step || 0, error: data.error || 'Unknown error' }))
            setIsRunning(false)
          }
          const msg = eventToMessage(data)
          if (msg) addStatusUpdate(msg)

          if (data.type === 'tool_result' && data.success && data.tool === 'push_report') {
            fetchReports()
          }
        }
      } catch (e) {
        console.error('Failed to parse monitor event:', e)
      }
    }

    websocket.onerror = () => {
      wsStateRef.current = 'disconnected'
      setWsConnected(false)
    }
    websocket.onclose = () => {
      const wasOurs = wsRef.current === websocket
      wsStateRef.current = 'disconnected'
      setWsConnected(false)
      if (wasOurs && isRunningRef.current) {
        addStatusUpdate({ taskType: 'analysis', text: `📡 ${t('agent.monitor_disconnected')}`, type: 'system' })
        // Auto-reconnect only if agent is still believed to be running
        scheduleReconnect()
      }
    }
    wsRef.current = websocket
  }, [addStatusUpdate, addCumulativeTokens, fetchReports, scheduleReconnect])

  // Keep stable refs for callbacks
  connectMonitorRef.current = connectMonitor
  isRunningRef.current = isRunning

  // Handlers
  const handleStartAgent = async () => {
    // Show modal immediately (step 0 = waiting for first progress event)
    setStartupModal({ step: 0, error: null })
    try {
      const result = await api.startAutonomousAgent(llmProvider, {
        model: model.trim() || undefined,
      })
      if (!result.success) {
        setStartupModal({ step: 0, error: result.error || result.detail || 'Unknown error' })
        return
      }
      setIsRunning(true)
      configSyncedRef.current = true
      wsReconnectAttemptsRef.current = 0
      addStatusUpdate({ taskType: 'analysis', text: `🚀 ${t('agent.agent_starting')}`, type: 'system' })
      connectMonitor()
      setTimeout(() => { checkAgentStatus(); fetchReports() }, 2000)
    } catch (error) {
      setStartupModal({ step: 0, error: error.message || error.toString() })
    }
  }

  const handleStopAgent = async () => {
    // Cancel any pending reconnect
    if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
    wsReconnectAttemptsRef.current = 0
    setWsReconnecting(false)
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null }
    wsStateRef.current = 'disconnected'
    try {
      await api.stopAutonomousAgent()
      setIsRunning(false)
      setAgentStatus(null)
      setWsConnected(false)
      configSyncedRef.current = false
      addStatusUpdate({ taskType: 'analysis', text: `🛑 ${t('agent.agent_stopped_by_user')}`, type: 'system' })
    } catch (error) {
      console.error('Failed to stop agent:', error)
    }
  }

  // Effects
  useEffect(() => {
    // Load defaults first (for placeholder models)
    api.getDefaults().then((res) => {
      if (res.model) setDefaultModel(res.model)
      if (res.provider_models) setProviderModels(res.provider_models)
    }).catch(() => {})

    // Try to get config from running agent or last-config, fallback to localStorage
    api.getAgentLastConfig().then((res) => {
      if (res.success && res.config) {
        const cfg = res.config
        if (cfg.llm_provider) setLlmProvider(cfg.llm_provider)
        if (cfg.model) setModel(cfg.model)
      } else {
        const stored = loadStoredConfig()
        if (stored) {
          setLlmProvider(stored.llmProvider)
          setModel(stored.model)
        }
      }
    }).catch(() => {
      const stored = loadStoredConfig()
      if (stored) {
        setLlmProvider(stored.llmProvider)
        setModel(stored.model)
      }
    })
  }, [])

  useEffect(() => {
    saveConfig({ llmProvider, model })
  }, [llmProvider, model])

  useEffect(() => {
    fetchActivityLog()
    checkAgentStatus()
    fetchReports()
    // Poll agent status every 5 seconds — this is the authoritative source for isRunning
    const statusInterval = setInterval(checkAgentStatus, 5000)
    const reportInterval = setInterval(fetchReports, 10000)
    return () => { clearInterval(statusInterval); clearInterval(reportInterval) }
  }, [fetchActivityLog, checkAgentStatus, fetchReports])

  useEffect(() => {
    if (isRunning && wsStateRef.current === 'disconnected' && !wsReconnectTimerRef.current) {
      connectMonitor()
    }
    // If agent stopped, cancel any pending reconnect
    if (!isRunning) {
      if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
      wsReconnectAttemptsRef.current = 0
      setWsReconnecting(false)
    }
  }, [isRunning, connectMonitor])

  useEffect(() => {
    return () => {
      if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
      if (wsRef.current) { wsRef.current.close(); wsRef.current = null }
      wsStateRef.current = 'disconnected'
    }
  }, [])

  // Filtered logs
  const filteredLogs = logFilter === 'all'
    ? logUpdates
    : logUpdates.filter(u => {
        const tt = u.message?.taskType || 'analysis'
        if (logFilter === 'chat') return tt === 'chat'
        return tt !== 'chat' // 'analysis' filter shows analysis + scheduled
      })

  // Render
  return (
    <div className="space-y-6">
      {startupModal && (
        <StartupModal
          currentStep={startupModal.step}
          error={startupModal.error}
          onClose={() => setStartupModal(null)}
        />
      )}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-3xl font-bold text-gray-900">{t('agent.title')}</h2>
          <p className="mt-1 text-sm text-gray-500">{t('agent.subtitle')}</p>
        </div>
        <div className="flex items-center space-x-3">
          <div className="flex items-center space-x-1 text-xs">
            {wsConnected ? (
              <><Wifi className="w-3 h-3 text-green-500" /><span className="text-green-600">{t('agent.live')}</span></>
            ) : isRunning && wsReconnecting ? (
              <><WifiOff className="w-3 h-3 text-orange-500 animate-pulse" /><span className="text-orange-600">{t('agent.reconnecting')}</span></>
            ) : isRunning ? (
              <><WifiOff className="w-3 h-3 text-yellow-500" /><span className="text-yellow-600">{t('agent.polling')}</span></>
            ) : null}
          </div>
          <button
            onClick={isRunning ? handleStopAgent : handleStartAgent}
            className={`btn ${isRunning ? 'btn-danger' : 'btn-primary'} flex items-center space-x-2`}
          >
            {isRunning ? (<><Square className="w-4 h-4" /><span>{t('agent.stop_agent')}</span></>) : (<><Play className="w-4 h-4" /><span>{t('agent.start_agent')}</span></>)}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Configuration */}
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">{t('agent.configuration')}</h3>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('agent.llm_provider')}</label>
              <select value={llmProvider} onChange={(e) => setLlmProvider(e.target.value)} className="select" disabled={isRunning}>
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
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('agent.model')}</label>
              <input
                type="text" value={model} onChange={(e) => setModel(e.target.value)}
                placeholder={providerModels[llmProvider] || ''}
                className="input w-full placeholder:text-gray-400" disabled={isRunning}
              />
              <p className="mt-1 text-xs text-gray-400">
                {model ? '' : providerModels[llmProvider] ? t('agent.using_default', { model: providerModels[llmProvider] }) : t('agent.leave_empty_default')}
              </p>
            </div>
          </div>
        </div>

        {/* Agent Status */}
        <div className="card">
          <div className="flex items-center space-x-2 mb-4">
            <Brain className="w-5 h-5 text-gray-600" />
            <h3 className="text-lg font-semibold text-gray-900">{t('agent.agent_status')}</h3>
          </div>
          {isRunning && agentStatus ? (
            <div className="space-y-3">
              <div className="flex items-center space-x-2">
                <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                <span className="text-sm text-gray-600">{t('agent.running')}</span>
              </div>
              {(agentStatus.config?.model || agentStatus.config?.llm_provider) && (
                <div className="text-sm space-y-1 pb-2 border-b border-gray-100">
                  {agentStatus.config?.model && (
                    <div className="flex justify-between">
                      <span className="text-gray-600">{t('agent.model')}:</span>
                      <span className="font-medium text-gray-900 font-mono text-xs">{agentStatus.config.model}</span>
                    </div>
                  )}
                </div>
              )}
              <div className="text-sm space-y-1">
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('agent.tasks_completed')}</span>
                  <span className="font-medium">{agentStatus.status?.cycle_count || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('agent.queue')}</span>
                  <span className="font-medium">{t('agent.pending', { count: agentStatus.status?.analysis_queue_size || 0 })}</span>
                </div>
                <div className="flex justify-between items-start">
                  <span className="text-gray-600">{t('agent.state')}</span>
                  <span className="font-medium text-xs bg-gray-100 px-2 py-0.5 rounded text-right">
                    {formatAgentState(agentStatus.status)}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-gray-400">
              <Activity className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p className="text-sm">{t('agent.agent_not_running')}</p>
            </div>
          )}
          {(cumulativeTokens.prompt > 0 || cumulativeTokens.thoughts > 0 || cumulativeTokens.response > 0) && (
            <div className="mt-3 pt-3 border-t border-gray-100">
              <div className="text-xs font-medium text-gray-500 mb-2">{t('agent.token_usage')}</div>
              <div className="grid grid-cols-2 gap-2 text-center">
                <div className="bg-amber-50 rounded px-2 py-1.5 border border-amber-100">
                  <div className="text-amber-700 font-mono font-semibold">{cumulativeTokens.prompt.toLocaleString()}</div>
                  <div className="text-amber-600 text-[10px]">{t('agent.tok_input')}</div>
                </div>
                <div className="bg-violet-50 rounded px-2 py-1.5 border border-violet-100">
                  <div className="text-violet-700 font-mono font-semibold">{cumulativeTokens.thoughts.toLocaleString()}</div>
                  <div className="text-violet-600 text-[10px]">{t('agent.tok_thinking')}</div>
                </div>
                <div className="bg-emerald-50 rounded px-2 py-1.5 border border-emerald-100">
                  <div className="text-emerald-700 font-mono font-semibold">{cumulativeTokens.response.toLocaleString()}</div>
                  <div className="text-emerald-600 text-[10px]">{t('agent.tok_response')}</div>
                </div>
                <div className="bg-sky-50 rounded px-2 py-1.5 border border-sky-100">
                  <div className="text-sky-700 font-mono font-semibold">{(cumulativeTokens.cacheRead || 0).toLocaleString()}</div>
                  <div className="text-sky-600 text-[10px]">{t('agent.tok_cached')}</div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Data Store */}
        <div className="card">
          <div className="flex items-center space-x-2 mb-4">
            <Database className="w-5 h-5 text-gray-600" />
            <h3 className="text-lg font-semibold text-gray-900">{t('agent.data_store')}</h3>
          </div>
          {isRunning && agentStatus?.data_store_stats ? (
            <div className="space-y-3">
              <div className="text-sm space-y-1">
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('agent.total_records')}</span>
                  <span className="font-medium">{(agentStatus.data_store_stats.total_records || 0).toLocaleString()}</span>
                </div>
              </div>
              {agentStatus.data_store_stats.by_feature && (
                <div className="pt-3 border-t border-gray-200">
                  <div className="text-xs text-gray-600 space-y-1 max-h-60 overflow-y-auto pr-2">
                    {Object.entries(agentStatus.data_store_stats.by_feature).map(([feature, count]) => (
                      <div key={feature} className="flex justify-between">
                        <span className="capitalize">{feature}:</span>
                        <span>{count.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {agentStatus.data_store_stats.time_range && (
                <div className="pt-3 border-t border-gray-200">
                  <div className="text-xs text-gray-600">
                    <div className="font-medium mb-1">{t('agent.time_range')}</div>
                    {agentStatus.data_store_stats.time_range.min && (<div>{t('agent.time_from')} {formatFullDateTime(agentStatus.data_store_stats.time_range.min)}</div>)}
                    {agentStatus.data_store_stats.time_range.max && (<div>{t('agent.time_to')} {formatFullDateTime(agentStatus.data_store_stats.time_range.max)}</div>)}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8 text-gray-400">
              <Database className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p className="text-sm">{t('agent.no_data')}</p>
            </div>
          )}
        </div>

      </div>

      {/* Second row: Scheduled Tasks + Agent Log */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Scheduled Tasks — 1/3 width, aligned with top row */}
        <div className="lg:col-span-1 space-y-6">
          <ScheduledTasksPanel isRunning={isRunning} />
          <TriggerRulesPanel isRunning={isRunning} />
        </div>

        {/* Unified Agent Log — 2/3 width */}
        <div className="lg:col-span-2 card flex flex-col overflow-hidden" style={{ height: '800px' }}>
        <div className="flex items-center justify-between mb-3 flex-shrink-0">
          <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary-500" />
            {t('agent.agent_activity')}
          </h3>
          <div className="flex items-center gap-2">
            {/* Filter buttons */}
            {['all', 'analysis', 'chat'].map((f) => (
              <button
                key={f}
                onClick={() => setLogFilter(f)}
                className={`text-xs px-2 py-0.5 rounded font-medium ${logFilter === f ? 'bg-primary-100 text-primary-700' : 'text-gray-400 hover:text-gray-600'}`}
              >
                {t(`agent.${f}`)}
              </button>
            ))}
            {logUpdates.length > 0 && (
              <button
                onClick={() => { if (window.confirm(t('agent.confirm_clear_logs'))) setLogUpdates([]) }}
                className="text-xs px-2 py-0.5 rounded font-medium ml-3 bg-red-50 text-red-500 hover:bg-red-100 hover:text-red-700 border border-red-200/60"
              >{t('agent.clear')}</button>
            )}
          </div>
        </div>
        <div
          className="bg-gray-50 rounded p-4 overflow-y-auto font-mono text-[11px] border border-gray-100 shadow-inner flex-1"
        >
          {/* Live streaming preview */}
          {(liveStream.thinking || liveStream.content) && (
            <div className="mb-3 pb-2 border-b-2 border-indigo-200/60">
              {liveStream.thinking && (
                <div className="mb-1 whitespace-pre-wrap text-blue-600 italic">
                  <span className="inline-block w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse mr-1.5 align-middle" />
                  <span className="text-blue-400 font-semibold mr-1">{t('agent.thinking_label')}</span>
                  {liveStream.thinking.length > 2000 ? liveStream.thinking.slice(-2000) : liveStream.thinking}
                </div>
              )}
              {liveStream.content && (
                <div className="whitespace-pre-wrap text-indigo-800 font-medium bg-indigo-50/50 rounded px-2 py-1">
                  <span className="inline-block w-1.5 h-1.5 bg-indigo-500 rounded-full animate-pulse mr-1.5 align-middle" />
                  {stripToolCallXml(liveStream.content.length > 2000 ? liveStream.content.slice(-2000) : liveStream.content)}
                </div>
              )}
            </div>
          )}
          {filteredLogs.length === 0 && !liveStream.thinking && !liveStream.content ? (
            <div className="text-gray-400 text-center py-8">
              {isRunning ? t('agent.waiting_events') : t('agent.start_to_see')}
            </div>
          ) : (
            filteredLogs.map((update, idx) => (<LogItem key={`${update.time}_${update.message?.type}_${update.message?.text?.slice(0, 40)}_${idx}`} update={update} />))
          )}
        </div>
      </div>
      </div>

      {/* Report Modal */}
      {selectedReport && (
        <div
          className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm animate-in fade-in duration-200"
          onClick={() => setSelectedReport(null)}
        >
          <div
            className="bg-white rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden shadow-2xl flex flex-col transform animate-in zoom-in-95 duration-200"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-start justify-between p-6 border-b border-gray-100 bg-white sticky top-0 z-10">
              <div>
                <div className="flex items-center space-x-3 mb-2">
                  <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide border ${selectedReport.alert_level === 'critical' ? 'bg-red-50 text-red-700 border-red-100' : selectedReport.alert_level === 'warning' ? 'bg-yellow-50 text-yellow-700 border-yellow-100' : 'bg-green-50 text-green-700 border-green-100'}`}>
                    {selectedReport.alert_level || 'normal'}
                  </span>
                  <span className="text-xs text-gray-400 uppercase tracking-widest font-semibold flex items-center">
                    <Calendar className="w-3 h-3 mr-1" />
                    {formatFullDateTime(selectedReport.created_at)}
                  </span>
                </div>
                <h2 className="text-2xl font-bold text-gray-900 leading-tight">
                  {selectedReport.title || t('agent.health_analysis_report')}
                </h2>
              </div>
              <button onClick={() => setSelectedReport(null)} className="p-2 hover:bg-gray-100 rounded-full transition-colors text-gray-400 hover:text-gray-600">
                <X className="w-6 h-6" />
              </button>
            </div>
            <div className="p-8 overflow-y-auto font-serif text-base leading-7 text-gray-800 bg-gray-50">
              <div className="prose prose-indigo max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedReport.content || ''}</ReactMarkdown>
              </div>
            </div>
            <div className="p-4 border-t border-gray-100 bg-white flex justify-between items-center text-xs text-gray-400">
              <div>{t('reports.report_id')} {selectedReport.id}</div>
              <button onClick={() => setSelectedReport(null)} className="btn bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium px-6">{t('common.close')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
