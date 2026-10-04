import { useState, useEffect, useRef } from 'react'
import { useTranslation, Trans } from 'react-i18next'
import { api } from '../lib/api.js'
import { 
  Save,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  User,
  Heart,
  Layers,
  Lock,
  Unlock,
  MessageSquare,
  Edit3
} from 'lucide-react'
import Skeleton from '../components/Skeleton'

const PROMPT_ICONS = {
  soul: Heart,
  experience: Layers,
  user: User,
}

const VISIBLE_PROMPTS = new Set(['soul', 'experience', 'user'])

export default function PromptEditor() {
  const { t } = useTranslation()
  const [prompts, setPrompts] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const selectedIdRef = useRef(selectedId)
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null) // { type: 'success' | 'error', message: string }
  const statusTimerRef = useRef(null)

  // Never leave the "saved" timer running after unmount.
  useEffect(() => () => {
    if (statusTimerRef.current) clearTimeout(statusTimerRef.current)
  }, [])

  // Mirror of selectedId readable from async callbacks without re-creating them
  useEffect(() => {
    selectedIdRef.current = selectedId
  }, [selectedId])

  /** True when the editor holds edits that were never saved. */
  const hasUnsavedEdits = () => {
    const current = prompts.find(p => p.id === selectedIdRef.current)
    return !!current && content !== current.content
  }

  const loadPrompts = async () => {
    const dirtyAtStart = hasUnsavedEdits()
    setLoading(true)
    try {
      const res = await api.listPrompts()
      if (res.success) {
        const filtered = res.prompts.filter(p => VISIBLE_PROMPTS.has(p.id))
        setPrompts(filtered)
        const currentSelectedId = selectedIdRef.current
        if (filtered.length > 0 && !currentSelectedId) {
          setSelectedId(filtered[0].id)
          setContent(filtered[0].content)
        } else if (currentSelectedId) {
          const selected = filtered.find(p => p.id === currentSelectedId)
          // Never silently drop unsaved edits when refreshing.
          if (selected && (!dirtyAtStart || window.confirm(t('prompts.confirm_discard')))) {
            setContent(selected.content)
          }
        }
      } else {
        setStatus({
          type: 'error',
          message: t('prompts.failed_prefix', { error: res.error || t('prompts.server_unsuccessful') })
        })
      }
    } catch (err) {
      console.error('Failed to load prompts:', err)
      setStatus({ type: 'error', message: t('prompts.load_error', { message: err.message || t('common.check_network') }) })
    } finally {
      setLoading(false)
    }
  }

  // Load once on mount. Deliberately not memoized into the dependency array:
  // loadPrompts closes over `t`, so a language switch would re-run it and pop the
  // "discard unsaved changes?" confirm at the user.
  //
  // set-state-in-effect is a false positive here — the only synchronous update is
  // setLoading(true), and `loading` already starts as true, so React bails out and
  // nothing cascades. Everything else happens after `await api.listPrompts()`.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPrompts()
  }, [])

  const handleSelect = (id) => {
    if (id === selectedId) return
    if (hasUnsavedEdits() && !window.confirm(t('prompts.confirm_discard'))) return
    setSelectedId(id)
    const selected = prompts.find(p => p.id === id)
    if (selected) {
      setContent(selected.content)
    }
    setStatus(null)
  }

  const handleSave = async () => {
    if (!selectedId || saving) return
    setSaving(true)
    if (statusTimerRef.current) { clearTimeout(statusTimerRef.current); statusTimerRef.current = null }
    setStatus(null)
    try {
      const res = await api.savePrompt(selectedId, content)
      if (res.success) {
        setStatus({ type: 'success', message: t('common.saved_successfully') })
        // Update local state
        setPrompts(prev => prev.map(p => p.id === selectedId ? { ...p, content } : p))
        statusTimerRef.current = setTimeout(() => {
          statusTimerRef.current = null
          setStatus(null)
        }, 3000)
      } else {
        setStatus({ type: 'error', message: res.error || t('common.save_failed') })
      }
    } catch (err) {
      console.error('Failed to save prompt:', err)
      setStatus({ type: 'error', message: t('prompts.save_failed_detail', { message: err.message || t('common.check_network') }) })
    } finally {
      setSaving(false)
    }
  }

  if (loading && prompts.length === 0) {
    return <Skeleton lines={4} label={t('prompts.loading_prompts')} />
  }

  const selectedPrompt = prompts.find(p => p.id === selectedId)

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="page-title">{t('prompts.title')}</h2>
          <p className="page-subtitle max-w-2xl">
            {t('prompts.subtitle')}
          </p>
        </div>
        <div className="flex items-center space-x-3">
          {status && (
            <div className={`flex items-center space-x-2 px-4 py-2 rounded-full text-sm font-semibold transition-all ${
              status.type === 'success' ? 'bg-ok/15 text-ok-ink' : 'bg-bad/15 text-bad-ink'
            }`}>
              {status.type === 'success' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
              <span>{status.message}</span>
            </div>
          )}
          <button
            onClick={loadPrompts}
            disabled={loading}
            title={t('prompts.refresh_tooltip')}
            className="btn-secondary"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            <span>{t('common.refresh')}</span>
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !selectedId}
            className="btn-primary"
          >
            {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            <span>{t('prompts.save_changes')}</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Sidebar / Tabs */}
        <div className="lg:col-span-4 space-y-3">
          <div className="text-sm font-bold text-ink-3 uppercase tracking-wider px-2 mb-2">{t('prompts.prompt_library')}</div>
          {prompts.map(p => {
            const Icon = PROMPT_ICONS[p.id] || MessageSquare
            const isActive = selectedId === p.id
            return (
              <button
                key={p.id}
                onClick={() => handleSelect(p.id)}
                className={`w-full text-left p-4 rounded-card transition-all border-2 flex items-center justify-between group ${
                  isActive 
                    ? 'bg-panel border-primary-500 shadow-md' 
                    : 'bg-panel border-transparent hover:border-line shadow-sm text-ink-2 hover:text-ink'
                }`}
              >
                <div className="flex items-center space-x-4">
                  <div className={`p-3 rounded-card transition-colors ${
                    isActive ? 'bg-primary-100 text-primary-600' : 'bg-sunken text-ink-3 group-hover:bg-line group-hover:text-ink-2'
                  }`}>
                    <Icon className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="font-bold text-lg">{p.title}</div>
                    <div className="text-sm opacity-60 font-medium">{p.file}</div>
                  </div>
                </div>
                <div className={`flex items-center px-2 py-1 rounded-chip text-[10px] font-bold uppercase tracking-tighter ${
                  p.agent_editable 
                    ? 'bg-info/10 text-info-ink border border-info/30' 
                    : 'bg-warn/10 text-warn-ink border border-warn/30'
                }`}>
                  {p.agent_editable ? (
                    <div className="flex items-center gap-1">
                      <Unlock className="w-3 h-3" /> {t('prompts.agent_editable')}
                    </div>
                  ) : (
                    <div className="flex items-center gap-1">
                      <Lock className="w-3 h-3" /> {t('prompts.user_only')}
                    </div>
                  )}
                </div>
              </button>
            )
          })}

          {/* Info Card */}
          <div className="note mt-8 p-6 relative overflow-hidden">
             <div className="relative z-10">
                <div className="flex items-center gap-2 mb-3">
                  <Edit3 className="w-5 h-5 text-primary-600" />
                  <h4 className="font-bold text-lg">{t('prompts.editor_note')}</h4>
                </div>
                <p className="text-ink-2 text-sm leading-relaxed mb-4">
                  <Trans i18nKey="prompts.editor_note_body_1" components={[<strong key="b" />]} />
                  <br/><br/>
                  <Trans i18nKey="prompts.editor_note_body_2" components={[<strong key="b" />]} />
                </p>
             </div>
             {/* Decorative blob */}
             <div className="absolute -right-10 -bottom-10 w-40 h-40 bg-primary-300/20 rounded-full blur-3xl"></div>
          </div>
        </div>

        {/* Editor Area */}
        <div className="lg:col-span-8 space-y-4 h-full flex flex-col">
          <div className="card h-full flex flex-col p-1 bg-sunken border-line shadow-inner min-h-[600px] relative">
            {/* Legend Overlay for Agent-Editable sections if applicable */}
            {selectedPrompt?.agent_editable && (
               <div className="absolute top-4 right-6 z-10 flex items-center space-x-2 text-[10px] font-bold uppercase bg-panel/80 backdrop-blur-sm px-3 py-1.5 rounded-full border border-info/30 shadow-sm pointer-events-none">
                  <div className="w-2 h-2 rounded-full bg-info animate-pulse"></div>
                  <span className="text-info-ink">{t('prompts.dynamic_evolution')}</span>
               </div>
            )}
            
            <label htmlFor="prompt-content" className="sr-only">{selectedPrompt?.title || t('prompts.this_prompt')}</label>
            <textarea
              id="prompt-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="w-full flex-1 p-8 bg-panel rounded-card border-none focus:ring-0 font-mono text-sm leading-relaxed text-ink resize-none shadow-sm placeholder-ink-3"
              placeholder={t('prompts.writer_placeholder', { title: selectedPrompt?.title || t('prompts.this_prompt') })}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                  e.preventDefault()
                  if (!saving) handleSave()
                }
              }}
            />
          </div>
          <div className="flex items-center justify-between px-2 text-xs text-ink-3 font-medium font-mono">
            <div>{t('prompts.tokens_lines', { tokens: Math.ceil(content.length / 4), lines: content.split('\n').length })}</div>
            <div>{t('prompts.shortcut_save')}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
