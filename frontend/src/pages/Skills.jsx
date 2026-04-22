import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api.js'
import {
  Save,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Sparkles,
  Plus,
  Trash2,
  BookOpen,
  Search,
  Eye,
  EyeOff,
  CheckSquare,
  Square,
} from 'lucide-react'

const NAME_RE = /^[a-z0-9_-]+$/

export default function Skills() {
  const { t } = useTranslation()
  const [skills, setSkills] = useState([])
  const [selectedName, setSelectedName] = useState(null)
  const selectedNameRef = useRef(selectedName)
  selectedNameRef.current = selectedName

  // Editor state
  const [editorName, setEditorName] = useState('')
  const [editorDescription, setEditorDescription] = useState('')
  const [editorBody, setEditorBody] = useState('')
  const [isNew, setIsNew] = useState(false)

  // List/filter state
  const [search, setSearch] = useState('')
  const [filterMode, setFilterMode] = useState('all') // all | enabled | disabled

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null) // {type, message}

  useEffect(() => {
    loadSkills()
  }, [])

  const loadSkills = async () => {
    setLoading(true)
    try {
      const res = await api.listSkills()
      if (res.success) {
        setSkills(res.skills || [])
        const current = selectedNameRef.current
        if (current && (res.skills || []).find((s) => s.name === current)) {
          await loadSkill(current)
        }
      } else {
        setStatus({ type: 'error', message: res.error || t('skills.failed_list_skills') })
      }
    } catch (err) {
      setStatus({ type: 'error', message: err.message })
    } finally {
      setLoading(false)
    }
  }

  const loadSkill = async (name) => {
    setIsNew(false)
    setSelectedName(name)
    const res = await api.fetchSkill(name)
    if (res.success) {
      setEditorName(res.name)
      setEditorDescription(res.description || '')
      setEditorBody(res.body || '')
      setStatus(null)
    } else {
      setStatus({ type: 'error', message: res.error || t('skills.failed_load_named', { name }) })
    }
  }

  const handleNew = () => {
    setIsNew(true)
    setSelectedName(null)
    setEditorName('')
    setEditorDescription('')
    setEditorBody('# New analysis playbook\n\n1. ...\n')
    setStatus(null)
  }

  const handleSave = async () => {
    if (!editorDescription.trim()) {
      setStatus({ type: 'error', message: t('skills.description_required') })
      return
    }
    setSaving(true)
    setStatus(null)
    try {
      let res
      if (isNew) {
        if (!NAME_RE.test(editorName)) {
          setStatus({
            type: 'error',
            message: t('skills.invalid_name'),
          })
          setSaving(false)
          return
        }
        res = await api.createSkill(editorName, editorDescription, editorBody)
      } else {
        res = await api.updateSkill(editorName, editorDescription, editorBody)
      }
      if (res.success) {
        setStatus({ type: 'success', message: t('skills.saved') })
        setIsNew(false)
        setSelectedName(editorName)
        await loadSkills()
        setTimeout(() => setStatus(null), 2500)
      } else {
        setStatus({ type: 'error', message: res.error || t('skills.save_failed') })
      }
    } catch (err) {
      setStatus({ type: 'error', message: err.message })
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (isNew || !editorName) return
    if (!window.confirm(t('skills.confirm_delete', { name: editorName }))) return
    setSaving(true)
    setStatus(null)
    try {
      const res = await api.deleteSkill(editorName)
      if (res.success) {
        setStatus({ type: 'success', message: t('skills.deleted') })
        setEditorName('')
        setEditorDescription('')
        setEditorBody('')
        setSelectedName(null)
        await loadSkills()
        setTimeout(() => setStatus(null), 2500)
      } else {
        setStatus({ type: 'error', message: res.error || t('skills.delete_failed') })
      }
    } catch (err) {
      setStatus({ type: 'error', message: err.message })
    } finally {
      setSaving(false)
    }
  }

  // ------------------------------------------------------------------
  // Enable/disable handling
  // ------------------------------------------------------------------

  const persistDisabledSet = async (nextSkills) => {
    const disabled = nextSkills.filter((s) => !s.enabled).map((s) => s.name)
    try {
      const res = await api.setSkillState(disabled)
      if (!res.success) {
        setStatus({ type: 'error', message: res.error || t('skills.failed_update_visibility') })
        // Reload to reconcile
        await loadSkills()
      }
    } catch (err) {
      setStatus({ type: 'error', message: err.message })
      await loadSkills()
    }
  }

  const toggleSkill = async (name) => {
    const next = skills.map((s) =>
      s.name === name ? { ...s, enabled: !s.enabled } : s,
    )
    setSkills(next) // optimistic
    await persistDisabledSet(next)
  }

  const setVisibilityForList = async (names, enabled) => {
    const target = new Set(names)
    const next = skills.map((s) => (target.has(s.name) ? { ...s, enabled } : s))
    setSkills(next)
    await persistDisabledSet(next)
  }

  // ------------------------------------------------------------------
  // Derived state
  // ------------------------------------------------------------------

  const filteredSkills = useMemo(() => {
    const q = search.trim().toLowerCase()
    return skills.filter((s) => {
      if (filterMode === 'enabled' && !s.enabled) return false
      if (filterMode === 'disabled' && s.enabled) return false
      if (!q) return true
      return (
        s.name.toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q)
      )
    })
  }, [skills, search, filterMode])

  const enabledCount = skills.filter((s) => s.enabled).length
  const totalCount = skills.length
  const filteredEnabledCount = filteredSkills.filter((s) => s.enabled).length
  const allFilteredEnabled =
    filteredSkills.length > 0 && filteredSkills.every((s) => s.enabled)

  if (loading && skills.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <RefreshCw className="w-8 h-8 text-primary-500 animate-spin" />
        <p className="text-gray-500 font-medium">{t('skills.loading')}</p>
      </div>
    )
  }

  const editorActive = isNew || selectedName !== null

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-4xl font-extrabold text-gray-900 tracking-tight">{t('skills.title')}</h2>
          <p className="mt-2 text-base text-gray-500 max-w-2xl">
            {t('skills.subtitle_1')}{' '}
            <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">read_skill</code>.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          {status && (
            <div
              className={`flex items-center space-x-2 px-4 py-2 rounded-full text-sm font-semibold transition-all ${
                status.type === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
              }`}
            >
              {status.type === 'success' ? (
                <CheckCircle2 className="w-4 h-4" />
              ) : (
                <AlertCircle className="w-4 h-4" />
              )}
              <span>{status.message}</span>
            </div>
          )}
          {editorActive && !isNew && (
            <button
              onClick={handleDelete}
              disabled={saving}
              className="btn flex items-center space-x-2 px-4 py-2.5 bg-red-50 text-red-600 hover:bg-red-100 transition-all active:scale-95 disabled:opacity-50 rounded-lg"
            >
              <Trash2 className="w-4 h-4" />
              <span className="font-bold text-sm">{t('skills.delete')}</span>
            </button>
          )}
          {editorActive && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="btn btn-primary flex items-center space-x-2 px-6 py-2.5 shadow-lg shadow-primary-200/50 hover:shadow-primary-300/50 transition-all active:scale-95 disabled:opacity-50"
            >
              {saving ? (
                <RefreshCw className="w-5 h-5 animate-spin" />
              ) : (
                <Save className="w-5 h-5" />
              )}
              <span className="text-base font-bold text-white">{t('skills.save')}</span>
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Sidebar — skill library */}
        <div className="lg:col-span-5 xl:col-span-4 space-y-3">
          {/* Counter + New */}
          <div className="card p-4 bg-gradient-to-br from-primary-50 to-white border border-primary-100">
            <div className="flex items-center justify-between mb-3">
              <div>
                <div className="text-2xl font-extrabold text-gray-900">
                  {enabledCount}
                  <span className="text-base font-bold text-gray-400">/{totalCount}</span>
                </div>
                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  {t('skills.visible_to_agent')}
                </div>
              </div>
              <button
                onClick={handleNew}
                className="flex items-center gap-1.5 text-xs font-bold text-white bg-primary-600 hover:bg-primary-700 px-3 py-2 rounded-lg shadow-sm transition-colors"
              >
                <Plus className="w-3.5 h-3.5" /> {t('skills.new_button')}
              </button>
            </div>
            {/* Search */}
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('skills.search_placeholder')}
                className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary-200 focus:border-primary-400 bg-white"
              />
            </div>
            {/* Filter pills + bulk actions */}
            <div className="flex items-center justify-between mt-3 gap-2">
              <div className="flex bg-white rounded-lg border border-gray-200 p-0.5 text-xs">
                {[
                  { k: 'all', label: t('skills.filter_all') },
                  { k: 'enabled', label: t('skills.filter_visible') },
                  { k: 'disabled', label: t('skills.filter_hidden') },
                ].map((opt) => (
                  <button
                    key={opt.k}
                    onClick={() => setFilterMode(opt.k)}
                    className={`px-2.5 py-1 rounded-md font-semibold transition-colors ${
                      filterMode === opt.k
                        ? 'bg-primary-100 text-primary-700'
                        : 'text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {filteredSkills.length > 0 && (
                <button
                  onClick={() =>
                    setVisibilityForList(
                      filteredSkills.map((s) => s.name),
                      !allFilteredEnabled,
                    )
                  }
                  className="flex items-center gap-1 text-xs font-bold text-primary-600 hover:text-primary-800 px-2 py-1 rounded transition-colors"
                  title={allFilteredEnabled ? t('skills.hide_all_tooltip') : t('skills.show_all_tooltip')}
                >
                  {allFilteredEnabled ? (
                    <>
                      <EyeOff className="w-3.5 h-3.5" /> {t('skills.hide_all')}
                    </>
                  ) : (
                    <>
                      <Eye className="w-3.5 h-3.5" /> {t('skills.show_all')}
                    </>
                  )}
                </button>
              )}
            </div>
            {search && (
              <div className="text-xs text-gray-400 mt-2 font-medium">
                {t('skills.match_count', { total: filteredSkills.length, visible: filteredEnabledCount })}
              </div>
            )}
          </div>

          {/* Empty / list */}
          {totalCount === 0 && !isNew && (
            <div className="p-6 bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200 text-center">
              <BookOpen className="w-8 h-8 text-gray-300 mx-auto mb-2" />
              <p className="text-sm text-gray-500">
                {t('skills.no_skills_yet')} <strong>{t('skills.new_button')}</strong> {t('skills.no_skills_yet_suffix')}
              </p>
            </div>
          )}

          {totalCount > 0 && filteredSkills.length === 0 && (
            <div className="p-6 bg-gray-50 rounded-2xl border border-dashed border-gray-200 text-center">
              <p className="text-sm text-gray-500">{t('skills.no_filter_match')}</p>
            </div>
          )}

          {/* Scrollable list */}
          {filteredSkills.length > 0 && (
            <div className="max-h-[calc(100vh-340px)] overflow-y-auto pr-1 -mr-1 space-y-1.5">
              {filteredSkills.map((s) => {
                const isActive = selectedName === s.name && !isNew
                return (
                  <div
                    key={s.name}
                    className={`group flex items-center gap-2 p-2.5 rounded-xl transition-all border ${
                      isActive
                        ? 'bg-white border-primary-400 shadow-md ring-1 ring-primary-200'
                        : s.enabled
                        ? 'bg-white border-gray-200 hover:border-primary-200 hover:shadow-sm'
                        : 'bg-gray-50 border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        toggleSkill(s.name)
                      }}
                      title={s.enabled ? t('skills.hide_from_agent') : t('skills.show_to_agent')}
                      className="flex-shrink-0 p-1 rounded-md hover:bg-gray-100 transition-colors"
                    >
                      {s.enabled ? (
                        <CheckSquare className="w-5 h-5 text-primary-600" />
                      ) : (
                        <Square className="w-5 h-5 text-gray-300" />
                      )}
                    </button>
                    <button
                      onClick={() => loadSkill(s.name)}
                      className="flex-1 min-w-0 text-left"
                    >
                      <div
                        className={`font-bold text-sm truncate ${
                          s.enabled ? 'text-gray-900' : 'text-gray-400'
                        }`}
                      >
                        {s.name}
                      </div>
                      <div
                        className={`text-xs truncate ${
                          s.enabled ? 'text-gray-500' : 'text-gray-400'
                        }`}
                      >
                        {s.description}
                      </div>
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {/* Info card */}
          <div className="mt-4 p-5 bg-gradient-to-br from-gray-900 to-gray-800 rounded-3xl text-white shadow-xl relative overflow-hidden">
            <div className="relative z-10">
              <div className="flex items-center gap-2 mb-2">
                <BookOpen className="w-5 h-5 text-primary-400" />
                <h4 className="font-bold text-base">{t('skills.progressive_disclosure')}</h4>
              </div>
              <p className="text-gray-300 text-xs leading-relaxed">
                {t('skills.progressive_body')}
              </p>
            </div>
            <div className="absolute -right-10 -bottom-10 w-32 h-32 bg-primary-500/20 rounded-full blur-3xl"></div>
          </div>
        </div>

        {/* Editor */}
        <div className="lg:col-span-7 xl:col-span-8 space-y-4 h-full flex flex-col">
          {!editorActive ? (
            <div className="card p-12 text-center bg-gray-50 border-dashed border-2 border-gray-200 min-h-[600px] flex flex-col items-center justify-center">
              <Sparkles className="w-12 h-12 text-gray-300 mb-4" />
              <p className="text-gray-500 font-medium">
                {t('skills.editor_empty')} <strong>{t('skills.new_button')}</strong> {t('skills.editor_empty_suffix')}
              </p>
            </div>
          ) : (
            <>
              {/* Name + description inputs */}
              <div className="card p-6 bg-white space-y-4">
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
                    {t('skills.name_label')}
                  </label>
                  <input
                    type="text"
                    value={editorName}
                    onChange={(e) => setEditorName(e.target.value)}
                    disabled={!isNew}
                    placeholder={t('skills.name_placeholder')}
                    className="w-full px-4 py-2.5 border border-gray-200 rounded-xl font-mono text-sm focus:ring-2 focus:ring-primary-200 focus:border-primary-400 disabled:bg-gray-50 disabled:text-gray-500"
                  />
                  {isNew && (
                    <p className="text-xs text-gray-400 mt-1.5">
                      {t('skills.name_hint')}
                    </p>
                  )}
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
                    {t('skills.description_label')}
                  </label>
                  <input
                    type="text"
                    value={editorDescription}
                    onChange={(e) => setEditorDescription(e.target.value)}
                    placeholder={t('skills.description_placeholder')}
                    className="w-full px-4 py-2.5 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-primary-200 focus:border-primary-400"
                  />
                </div>
              </div>

              {/* Body editor */}
              <div className="card h-full flex flex-col p-1 bg-gray-50 border-gray-200 shadow-inner min-h-[500px] relative">
                <textarea
                  value={editorBody}
                  onChange={(e) => setEditorBody(e.target.value)}
                  className="w-full flex-1 p-8 bg-white rounded-2xl border-none focus:ring-0 font-mono text-sm leading-relaxed text-gray-800 resize-none shadow-sm placeholder-gray-400"
                  placeholder="# Playbook body&#10;&#10;1. Use the sql tool to query ...&#10;2. Use the code tool to compute ...&#10;3. Decision rule: ..."
                  onKeyDown={(e) => {
                    if (e.ctrlKey && e.key === 's') {
                      e.preventDefault()
                      handleSave()
                    }
                  }}
                />
              </div>
              <div className="flex items-center justify-between px-2 text-xs text-gray-400 font-medium font-mono">
                <div>
                  {t('skills.tokens_lines', { tokens: Math.ceil(editorBody.length / 4), lines: editorBody.split('\n').length })}
                </div>
                <div>{t('skills.shortcut')}</div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
