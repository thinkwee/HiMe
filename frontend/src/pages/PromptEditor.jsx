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
  selectedIdRef.current = selectedId
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null) // { type: 'success' | 'error', message: string }

  useEffect(() => {
    loadPrompts()
  }, [])

  const loadPrompts = async () => {
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
          if (selected) setContent(selected.content)
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

  const handleSelect = (id) => {
    setSelectedId(id)
    const selected = prompts.find(p => p.id === id)
    if (selected) {
      setContent(selected.content)
    }
    setStatus(null)
  }

  const handleSave = async () => {
    if (!selectedId) return
    setSaving(true)
    setStatus(null)
    try {
      const res = await api.savePrompt(selectedId, content)
      if (res.success) {
        setStatus({ type: 'success', message: t('common.saved_successfully') })
        // Update local state
        setPrompts(prev => prev.map(p => p.id === selectedId ? { ...p, content } : p))
        setTimeout(() => setStatus(null), 3000)
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
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <RefreshCw className="w-8 h-8 text-primary-500 animate-spin" />
        <p className="text-gray-500 font-medium">{t('prompts.loading_prompts')}</p>
      </div>
    )
  }

  const selectedPrompt = prompts.find(p => p.id === selectedId)

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-4xl font-extrabold text-gray-900 tracking-tight">{t('prompts.title')}</h2>
          <p className="mt-2 text-base text-gray-500 max-w-2xl">
            {t('prompts.subtitle')}
          </p>
        </div>
        <div className="flex items-center space-x-3">
          {status && (
            <div className={`flex items-center space-x-2 px-4 py-2 rounded-full text-sm font-semibold transition-all ${
              status.type === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
            }`}>
              {status.type === 'success' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
              <span>{status.message}</span>
            </div>
          )}
          <button
            onClick={loadPrompts}
            disabled={loading}
            title={t('prompts.refresh_tooltip')}
            className="btn flex items-center space-x-2 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100"
          >
            <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
            <span className="text-base font-semibold">{t('common.refresh')}</span>
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !selectedId}
            className="btn btn-primary flex items-center space-x-2 px-6 py-2.5 shadow-lg shadow-primary-200/50 hover:shadow-primary-300/50 transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100"
          >
            {saving ? <RefreshCw className="w-5 h-5 animate-spin" /> : <Save className="w-5 h-5" />}
            <span className="text-base font-bold text-white">{t('prompts.save_changes')}</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Sidebar / Tabs */}
        <div className="lg:col-span-4 space-y-3">
          <div className="text-sm font-bold text-gray-400 uppercase tracking-wider px-2 mb-2">{t('prompts.prompt_library')}</div>
          {prompts.map(p => {
            const Icon = PROMPT_ICONS[p.id] || MessageSquare
            const isActive = selectedId === p.id
            return (
              <button
                key={p.id}
                onClick={() => handleSelect(p.id)}
                className={`w-full text-left p-4 rounded-2xl transition-all border-2 flex items-center justify-between group ${
                  isActive 
                    ? 'bg-white border-primary-500 shadow-xl shadow-primary-100/20 ring-1 ring-primary-500/10' 
                    : 'bg-white border-transparent hover:border-gray-200 shadow-sm text-gray-600 hover:text-gray-900'
                }`}
              >
                <div className="flex items-center space-x-4">
                  <div className={`p-3 rounded-xl transition-colors ${
                    isActive ? 'bg-primary-100 text-primary-600' : 'bg-gray-100 text-gray-400 group-hover:bg-gray-200 group-hover:text-gray-600'
                  }`}>
                    <Icon className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="font-bold text-lg">{p.title}</div>
                    <div className="text-sm opacity-60 font-medium">{p.file}</div>
                  </div>
                </div>
                <div className={`flex items-center px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-tighter ${
                  p.agent_editable 
                    ? 'bg-blue-50 text-blue-600 border border-blue-100' 
                    : 'bg-amber-50 text-amber-600 border border-amber-100'
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
          <div className="mt-8 p-6 bg-gradient-to-br from-gray-900 to-gray-800 rounded-3xl text-white shadow-2xl relative overflow-hidden">
             <div className="relative z-10">
                <div className="flex items-center gap-2 mb-3">
                  <Edit3 className="w-5 h-5 text-primary-400" />
                  <h4 className="font-bold text-lg">{t('prompts.editor_note')}</h4>
                </div>
                <p className="text-gray-300 text-sm leading-relaxed mb-4">
                  <Trans i18nKey="prompts.editor_note_body_1" components={[<strong />]} />
                  <br/><br/>
                  <Trans i18nKey="prompts.editor_note_body_2" components={[<strong />]} />
                </p>
             </div>
             {/* Decorative blob */}
             <div className="absolute -right-10 -bottom-10 w-40 h-40 bg-primary-500/20 rounded-full blur-3xl"></div>
          </div>
        </div>

        {/* Editor Area */}
        <div className="lg:col-span-8 space-y-4 h-full flex flex-col">
          <div className="card h-full flex flex-col p-1 bg-gray-50 border-gray-200 shadow-inner min-h-[600px] relative">
            {/* Legend Overlay for Agent-Editable sections if applicable */}
            {selectedPrompt?.agent_editable && (
               <div className="absolute top-4 right-6 z-10 flex items-center space-x-2 text-[10px] font-bold uppercase bg-white/80 backdrop-blur-sm px-3 py-1.5 rounded-full border border-blue-100 shadow-sm pointer-events-none">
                  <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></div>
                  <span className="text-blue-600">{t('prompts.dynamic_evolution')}</span>
               </div>
            )}
            
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="w-full flex-1 p-8 bg-white rounded-2xl border-none focus:ring-0 font-mono text-sm leading-relaxed text-gray-800 resize-none shadow-sm placeholder-gray-400"
              placeholder={t('prompts.writer_placeholder', { title: selectedPrompt?.title || t('prompts.this_prompt') })}
              onKeyDown={(e) => {
                if (e.ctrlKey && e.key === 's') {
                  e.preventDefault()
                  handleSave()
                }
              }}
            />
          </div>
          <div className="flex items-center justify-between px-2 text-xs text-gray-400 font-medium font-mono">
            <div>{t('prompts.tokens_lines', { tokens: Math.ceil(content.length / 4), lines: content.split('\n').length })}</div>
            <div>{t('prompts.shortcut_save')}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
