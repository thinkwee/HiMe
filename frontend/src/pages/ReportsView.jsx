import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { FileText, Calendar, Filter, Activity, ArrowDown, ArrowUp, RefreshCw, Search, X, Zap, Clock, AlertTriangle } from 'lucide-react'
import { parseBackendDate } from '../lib/utils'
import { useOnActivate } from '../lib/hooks'
import Skeleton from '../components/Skeleton'

// Helper to get report source from the report object
const getReportSource = (report) => {
  // Check top-level source field first, then metadata.source, then default
  return report.source || report.metadata?.source || 'scheduled_analysis'
}

// Render a source badge. Declared at module scope so it keeps a stable identity
// across renders instead of being re-created (and remounted) every time.
const SourceBadge = ({ report, size = 'sm', t }) => {
  const source = getReportSource(report)
  if (source === 'quick_analysis') {
    return (
      <span className={`inline-flex items-center gap-1 rounded-full font-semibold tracking-wide border ${
        size === 'sm'
          ? 'px-1.5 py-0.5 text-[10px]'
          : 'px-2.5 py-0.5 text-xs'
      } bg-warn/10 text-warn-ink border-warn/30`}>
        <Zap className={size === 'sm' ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
        {t('reports.badge_quick')}
      </span>
    )
  }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-semibold tracking-wide border ${
      size === 'sm'
        ? 'px-1.5 py-0.5 text-[10px]'
        : 'px-2.5 py-0.5 text-xs'
    } bg-info/10 text-info-ink border-info/30`}>
      <Clock className={size === 'sm' ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
      {t('reports.badge_scheduled')}
    </span>
  )
}

export default function ReportsView({ active = true }) {
  const { t } = useTranslation()
  // State
  const [reports, setReports] = useState([])
  // Starts true because the mount effect always fetches: initialising it here
  // instead of letting the effect flip it avoids a cascading render (and a brief
  // flash of the "no reports" empty state before the first fetch resolves).
  const [loading, setLoading] = useState(true)
  const [selectedReport, setSelectedReport] = useState(null)
  const [loadError, setLoadError] = useState('')
  const modalRef = useRef(null)

  // Filters
  const [alertFilter, setAlertFilter] = useState('all') // all, critical, warning, info, normal
  const [sortBy, setSortBy] = useState('data_time') // data_time, created_at
  const [sortOrder, setSortOrder] = useState('desc') // desc, asc
  const [searchQuery, setSearchQuery] = useState('')

  // `silent` refetches (route re-activation) keep the current list on screen
  // instead of flashing the skeleton.
  const loadReports = useCallback(async (silent = false) => {
    if (silent !== true) setLoading(true)
    try {
      const result = await api.queryAgentMemory('reports')
      if (result.success && Array.isArray(result.data)) {
        setReports(result.data)
        setLoadError('')
      } else {
        // Keep whatever is already shown; say why instead of pretending there are no reports.
        setLoadError(result.error || t('reports.load_failed'))
      }
    } catch (error) {
      console.error('Failed to load reports:', error)
      setLoadError(error?.message || t('reports.load_failed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  // Load reports on mount. loadReports is memoized with no dependencies, so this
  // still runs exactly once.
  //
  // set-state-in-effect is a false positive here — the only synchronous update is
  // setLoading(true), and `loading` already starts as true, so React bails out and
  // nothing cascades. Everything else happens after `await queryAgentMemory()`.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadReports()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The page stays mounted while hidden: pick up new reports when it becomes active again.
  useOnActivate(active, () => loadReports(true))

  // Report modal: Escape closes, focus moves into the dialog.
  useEffect(() => {
    if (!selectedReport) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setSelectedReport(null) }
    document.addEventListener('keydown', onKey)
    modalRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [selectedReport])

  // Filter and Sort Logic
  const filteredReports = useMemo(() => {
    let filtered = [...reports]

    // 1. Alert Level Filter
    if (alertFilter !== 'all') {
      filtered = filtered.filter(r => (r.alert_level || 'normal').toLowerCase() === alertFilter)
    }

    // 2. Search Query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(r =>
        (r.title || '').toLowerCase().includes(q) ||
        (r.content || '').toLowerCase().includes(q)
      )
    }

    // 3. Sorting
    filtered.sort((a, b) => {
      let valA, valB

      if (sortBy === 'data_time') {
        valA = parseBackendDate(a.metadata?.data_timestamp || a.time_range_end || 0).getTime()
        valB = parseBackendDate(b.metadata?.data_timestamp || b.time_range_end || 0).getTime()
      } else { // created_at
        valA = parseBackendDate(a.created_at || 0).getTime()
        valB = parseBackendDate(b.created_at || 0).getTime()
      }

      return sortOrder === 'desc' ? valB - valA : valA - valB
    })

    return filtered
  }, [reports, alertFilter, sortBy, sortOrder, searchQuery])

  // Helper to format timestamps
  const formatTime = (ts) => {
    if (!ts) return t('common.na')
    // Backend timestamps are UTC without a 'Z' — parse them as such, exactly
    // like the detail modal does.
    const d = parseBackendDate(ts)
    if (isNaN(d.getTime())) return t('common.na')
    const year = d.getFullYear()
    const month = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const hours = String(d.getHours()).padStart(2, '0')
    const minutes = String(d.getMinutes()).padStart(2, '0')
    const seconds = String(d.getSeconds()).padStart(2, '0')
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="page-title">{t('reports.title')}</h2>
          <p className="page-subtitle">
            {t('reports.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => loadReports()}
            className="btn-secondary flex items-center gap-2"
            disabled={loading}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            {t('reports.refresh')}
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="bg-panel p-4 rounded-card border border-line shadow-sm space-y-4 md:space-y-0 md:flex md:items-center md:justify-between">

        {/* Left: Search & Alert Filter */}
        <div className="flex flex-col md:flex-row gap-4 flex-1">
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <Search className="h-4 w-4 text-ink-3" />
            </div>
            <input
              type="text"
              placeholder={t('reports.search_placeholder')}
              aria-label={t('reports.search_placeholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10 pr-4 py-2 border border-line-2 rounded-control text-sm bg-panel text-ink focus:ring-accent/60 focus:border-accent-strong w-full md:w-64"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                aria-label={t('reports.clear_search')}
                className="absolute inset-y-0 right-0 pr-3 flex items-center text-ink-3 hover:text-ink-2"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 overflow-x-auto">
            <span className="text-sm font-medium text-ink-2 flex items-center gap-1">
              <Filter className="w-4 h-4" /> {t('reports.filter')}
            </span>
            {[
              { id: 'all', label: t('reports.filter_all') },
              { id: 'critical', label: t('reports.filter_critical'), color: 'bg-bad/15 text-bad-ink' },
              { id: 'warning', label: t('reports.filter_warning'), color: 'bg-warn/15 text-warn-ink' },
              { id: 'info', label: t('reports.filter_info'), color: 'bg-info/15 text-info-ink' },
              { id: 'normal', label: t('reports.filter_normal'), color: 'bg-ok/15 text-ok-ink' },
            ].map((type) => (
              <button
                type="button"
                key={type.id}
                onClick={() => setAlertFilter(type.id)}
                aria-pressed={alertFilter === type.id}
                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${alertFilter === type.id
                    ? type.color || 'bg-ink text-bg'
                    : 'bg-sunken text-ink-2 hover:bg-line'
                  }`}
              >
                {type.label}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Sort */}
        <div className="flex items-center gap-3 border-l pl-4 border-line">
          <span className="text-sm text-ink-2">{t('reports.sort_by')}</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            aria-label={t('reports.sort_by')}
            className="text-sm border-none bg-transparent focus:ring-0 font-medium text-ink cursor-pointer"
          >
            <option value="data_time">{t('reports.sort_data_time')}</option>
            <option value="created_at">{t('reports.sort_created_at')}</option>
          </select>
          <button
            type="button"
            onClick={() => setSortOrder(prev => prev === 'desc' ? 'asc' : 'desc')}
            className="p-1 rounded-chip hover:bg-sunken text-ink-2"
            title={sortOrder === 'desc' ? t('reports.newest_first') : t('reports.oldest_first')}
            aria-label={sortOrder === 'desc' ? t('reports.newest_first') : t('reports.oldest_first')}
          >
            {sortOrder === 'desc' ? <ArrowDown className="w-4 h-4" /> : <ArrowUp className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {loadError && (
        <div role="alert" className="flex items-center gap-3 rounded-card border border-bad/30 bg-bad/10 px-4 py-3 text-sm text-bad-ink">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
          <span className="flex-1 break-words">{t('reports.load_failed_detail', { error: loadError })}</span>
          <button type="button" onClick={() => loadReports()} className="font-semibold underline hover:text-bad-ink">
            {t('common.retry')}
          </button>
        </div>
      )}

      {/* Reports Grid */}
      {loading && reports.length === 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map(i => (
            <Skeleton key={i} lines={5} className="h-64" />
          ))}
        </div>
      ) : filteredReports.length === 0 ? (
        loadError ? null : <div className="text-center py-20 bg-sunken rounded-card border border-dashed border-line-2">
          <FileText className="w-12 h-12 mx-auto text-ink-3 mb-3" />
          <h3 className="text-lg font-medium text-ink">{t('reports.no_reports')}</h3>
          <p className="text-ink-2 text-sm">{t('reports.no_reports_hint')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredReports.map((report) => (
            <div
              key={report.id}
              role="button"
              tabIndex={0}
              onClick={() => setSelectedReport(report)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setSelectedReport(report)
                }
              }}
              className="focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 bg-panel rounded-card shadow-sm border border-line p-5 hover:shadow-md transition-all duration-200 group flex flex-col h-full relative overflow-hidden cursor-pointer"
            >
              {/* Alert Stripe */}
              <div className={`absolute top-0 left-0 w-1 h-full ${report.alert_level === 'critical' ? 'bg-bad' :
                  report.alert_level === 'warning' ? 'bg-warn' :
                    report.alert_level === 'info' ? 'bg-info' :
                      'bg-ok'
                }`} />

              {/* Header */}
              <div className="pl-3 mb-3">
                <div className="flex justify-between items-start mb-2">
                  <h3 className="font-bold text-ink leading-snug line-clamp-2" title={report.title}>
                    {report.title || t('reports.untitled')}
                  </h3>
                  <div className="flex items-center gap-1 ml-2 shrink-0">
                    <SourceBadge report={report} size="sm" t={t} />
                    <span className={`px-2 py-0.5 rounded-chip text-[10px] font-bold uppercase tracking-wide border whitespace-nowrap ${report.alert_level === 'critical' ? 'bg-bad/10 text-bad-ink border-bad/30' :
                        report.alert_level === 'warning' ? 'bg-warn/10 text-warn-ink border-warn/30' :
                          report.alert_level === 'info' ? 'bg-info/10 text-info-ink border-info/30' :
                            'bg-ok/10 text-ok-ink border-ok/30'
                      }`}>
                      {report.alert_level || 'normal'}
                    </span>
                  </div>
                </div>

                {/* Dual Timestamps */}
                <div className="grid grid-cols-2 gap-2 text-[10px] text-ink-2 bg-sunken p-2 rounded-chip border border-line">
                  <div>
                    <div className="font-medium text-ink-3 mb-0.5 flex items-center gap-1">
                      <Activity className="w-3 h-3" /> {t('reports.data_time')}
                    </div>
                    <div className="font-mono text-info-ink font-semibold truncate">
                      {formatTime(report.metadata?.data_timestamp || report.time_range_end)}
                    </div>
                  </div>
                  <div className="border-l border-line pl-2">
                    <div className="font-medium text-ink-3 mb-0.5 flex items-center gap-1">
                      <Calendar className="w-3 h-3" /> {t('reports.generated')}
                    </div>
                    <div className="font-mono truncate">
                      {formatTime(report.created_at)}
                    </div>
                  </div>
                </div>
              </div>

              {/* Content */}
              <div className="pl-3 flex-grow">
                <div className="text-sm text-ink-2 leading-relaxed font-serif line-clamp-4 max-h-24 overflow-hidden relative">
                  {(report.content || '').replace(/[#*`]/g, '')}
                  <div className="absolute bottom-0 left-0 w-full h-6 bg-gradient-to-t from-panel to-transparent" />
                </div>
              </div>

              {/* Footer */}
              <div className="pl-3 pt-3 mt-3 border-t border-line flex justify-between items-center text-xs text-ink-3">
                <span>{t('reports.id_prefix')} {report.id}</span>
                {Array.isArray(report.metadata?.tags) && report.metadata.tags.length > 0 && (
                  <div className="flex gap-1">
                    {report.metadata.tags.slice(0, 2).map(tag => (
                      <span key={tag} className="bg-sunken px-1.5 py-0.5 rounded-chip text-ink-2">
                        #{tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Report Modal */}
      {selectedReport && (
        <div
          className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm animate-in fade-in duration-200"
          onClick={() => setSelectedReport(null)}
        >
          <div
            ref={modalRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="report-modal-title"
            className="bg-panel rounded-card w-full max-w-4xl max-h-[90vh] overflow-hidden shadow-2xl flex flex-col transform animate-in zoom-in-95 duration-200 outline-none"
            onClick={e => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-start justify-between p-6 border-b border-line bg-panel sticky top-0 z-10">
              <div>
                <div className="flex items-center space-x-3 mb-2">
                  <SourceBadge report={selectedReport} size="md" t={t} />
                  <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide border ${selectedReport.alert_level === 'critical' ? 'bg-bad/10 text-bad-ink border-bad/30' :
                      selectedReport.alert_level === 'warning' ? 'bg-warn/10 text-warn-ink border-warn/30' :
                        selectedReport.alert_level === 'info' ? 'bg-info/10 text-info-ink border-info/30' :
                          'bg-ok/10 text-ok-ink border-ok/30'
                    }`}>
                    {selectedReport.alert_level || 'normal'}
                  </span>
                  <span className="text-xs text-ink-3 uppercase tracking-widest font-semibold flex items-center">
                    <Calendar className="w-3 h-3 mr-1" />
                    {parseBackendDate(selectedReport.created_at).toLocaleString()}
                  </span>
                </div>
                <h2 id="report-modal-title" className="text-2xl font-bold text-ink leading-tight">
                  {selectedReport.title || t('reports.health_analysis_report')}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setSelectedReport(null)}
                aria-label={t('common.close')}
                className="p-2 hover:bg-sunken rounded-full transition-colors text-ink-3 hover:text-ink-2"
              >
                <X className="w-6 h-6" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-4 md:p-8 overflow-y-auto font-serif text-base leading-7 text-ink bg-sunken ">
              <div className="prose prose-hime max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {selectedReport.content || ''}
                </ReactMarkdown>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="p-4 border-t border-line bg-panel flex justify-between items-center text-xs text-ink-3">
              <div>
                {t('reports.report_id')} {selectedReport.id} • {t('reports.source')} {getReportSource(selectedReport) === 'quick_analysis' ? t('reports.source_quick') : t('reports.source_scheduled')} • {t('reports.data_time_label')} {selectedReport.metadata?.data_timestamp ? parseBackendDate(selectedReport.metadata.data_timestamp).toLocaleString() : t('common.na')}
              </div>
              <button
                onClick={() => setSelectedReport(null)}
                className="btn bg-sunken hover:bg-line text-ink font-medium px-6"
              >
                {t('reports.close_report')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
