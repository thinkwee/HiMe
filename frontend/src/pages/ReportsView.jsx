import { useState, useEffect, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { FileText, Calendar, Filter, Activity, ArrowDown, ArrowUp, Search, X, Zap, Clock } from 'lucide-react'
import { parseBackendDate } from '../lib/utils'

export default function ReportsView() {
  const { t } = useTranslation()
  // State
  const [reports, setReports] = useState([])
  const [loading, setLoading] = useState(false)
  const [selectedReport, setSelectedReport] = useState(null)

  // Filters
  const [alertFilter, setAlertFilter] = useState('all') // all, critical, warning, info, normal
  const [sortBy, setSortBy] = useState('data_time') // data_time, created_at
  const [sortOrder, setSortOrder] = useState('desc') // desc, asc
  const [searchQuery, setSearchQuery] = useState('')

  // Load reports on mount
  useEffect(() => {
    loadReports()
  }, [])

  const loadReports = async () => {
    setLoading(true)
    try {
      const result = await api.queryAgentMemory('reports')
      if (result.success && Array.isArray(result.data)) {
        setReports(result.data)
      } else {
        setReports([])
      }
    } catch (error) {
      console.error('Failed to load reports:', error)
      setReports([])
    } finally {
      setLoading(false)
    }
  }

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
    const d = new Date(ts)
    if (isNaN(d.getTime())) return t('common.na')
    const year = d.getFullYear()
    const month = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const hours = String(d.getHours()).padStart(2, '0')
    const minutes = String(d.getMinutes()).padStart(2, '0')
    const seconds = String(d.getSeconds()).padStart(2, '0')
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
  }

  // Helper to get report source from the report object
  const getReportSource = (report) => {
    // Check top-level source field first, then metadata.source, then default
    return report.source || report.metadata?.source || 'scheduled_analysis'
  }

  // Render a source badge
  const SourceBadge = ({ report, size = 'sm' }) => {
    const source = getReportSource(report)
    if (source === 'quick_analysis') {
      return (
        <span className={`inline-flex items-center gap-1 rounded-full font-semibold tracking-wide border ${
          size === 'sm'
            ? 'px-1.5 py-0.5 text-[10px]'
            : 'px-2.5 py-0.5 text-xs'
        } bg-amber-50 text-amber-700 border-amber-200`}>
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
      } bg-indigo-50 text-indigo-700 border-indigo-200`}>
        <Clock className={size === 'sm' ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
        {t('reports.badge_scheduled')}
      </span>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold text-gray-900">{t('reports.title')}</h2>
          <p className="mt-1 text-sm text-gray-500">
            {t('reports.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadReports}
            className="btn-secondary flex items-center gap-2"
            disabled={loading}
          >
            <ArrowUp className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            {t('reports.refresh')}
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="bg-white p-4 rounded-xl border border-gray-200 shadow-sm space-y-4 md:space-y-0 md:flex md:items-center md:justify-between">

        {/* Left: Search & Alert Filter */}
        <div className="flex flex-col md:flex-row gap-4 flex-1">
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <Search className="h-4 w-4 text-gray-400" />
            </div>
            <input
              type="text"
              placeholder={t('reports.search_placeholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:ring-indigo-500 focus:border-indigo-500 w-full md:w-64"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 overflow-x-auto">
            <span className="text-sm font-medium text-gray-500 flex items-center gap-1">
              <Filter className="w-4 h-4" /> {t('reports.filter')}
            </span>
            {[
              { id: 'all', label: t('reports.filter_all') },
              { id: 'critical', label: t('reports.filter_critical'), color: 'bg-red-100 text-red-700' },
              { id: 'warning', label: t('reports.filter_warning'), color: 'bg-yellow-100 text-yellow-700' },
              { id: 'info', label: t('reports.filter_info'), color: 'bg-blue-100 text-blue-700' },
              { id: 'normal', label: t('reports.filter_normal'), color: 'bg-green-100 text-green-700' },
            ].map((type) => (
              <button
                key={type.id}
                onClick={() => setAlertFilter(type.id)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${alertFilter === type.id
                    ? type.color || 'bg-gray-800 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
              >
                {type.label}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Sort */}
        <div className="flex items-center gap-3 border-l pl-4 border-gray-200">
          <span className="text-sm text-gray-500">{t('reports.sort_by')}</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="text-sm border-none bg-transparent focus:ring-0 font-medium text-gray-700 cursor-pointer"
          >
            <option value="data_time">{t('reports.sort_data_time')}</option>
            <option value="created_at">{t('reports.sort_created_at')}</option>
          </select>
          <button
            onClick={() => setSortOrder(prev => prev === 'desc' ? 'asc' : 'desc')}
            className="p-1 rounded hover:bg-gray-100 text-gray-500"
            title={sortOrder === 'desc' ? t('reports.newest_first') : t('reports.oldest_first')}
          >
            {sortOrder === 'desc' ? <ArrowDown className="w-4 h-4" /> : <ArrowUp className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Reports Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-pulse">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-64 bg-gray-200 rounded-xl"></div>
          ))}
        </div>
      ) : filteredReports.length === 0 ? (
        <div className="text-center py-20 bg-gray-50 rounded-xl border border-dashed border-gray-300">
          <FileText className="w-12 h-12 mx-auto text-gray-300 mb-3" />
          <h3 className="text-lg font-medium text-gray-900">{t('reports.no_reports')}</h3>
          <p className="text-gray-500 text-sm">{t('reports.no_reports_hint')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredReports.map((report) => (
            <div
              key={report.id}
              onClick={() => setSelectedReport(report)}
              className="bg-white rounded-xl shadow-sm border border-gray-200 p-5 hover:shadow-md transition-all duration-200 group flex flex-col h-full relative overflow-hidden cursor-pointer"
            >
              {/* Alert Stripe */}
              <div className={`absolute top-0 left-0 w-1 h-full ${report.alert_level === 'critical' ? 'bg-red-500' :
                  report.alert_level === 'warning' ? 'bg-yellow-500' :
                    report.alert_level === 'info' ? 'bg-blue-500' :
                      'bg-green-500'
                }`} />

              {/* Header */}
              <div className="pl-3 mb-3">
                <div className="flex justify-between items-start mb-2">
                  <h3 className="font-bold text-gray-900 leading-snug line-clamp-2" title={report.title}>
                    {report.title || t('reports.untitled')}
                  </h3>
                  <div className="flex items-center gap-1 ml-2 shrink-0">
                    <SourceBadge report={report} size="sm" />
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide border whitespace-nowrap ${report.alert_level === 'critical' ? 'bg-red-50 text-red-700 border-red-100' :
                        report.alert_level === 'warning' ? 'bg-yellow-50 text-yellow-700 border-yellow-100' :
                          report.alert_level === 'info' ? 'bg-blue-50 text-blue-700 border-blue-100' :
                            'bg-green-50 text-green-700 border-green-100'
                      }`}>
                      {report.alert_level || 'normal'}
                    </span>
                  </div>
                </div>

                {/* Dual Timestamps */}
                <div className="grid grid-cols-2 gap-2 text-[10px] text-gray-500 bg-gray-50 p-2 rounded border border-gray-100">
                  <div>
                    <div className="font-medium text-gray-400 mb-0.5 flex items-center gap-1">
                      <Activity className="w-3 h-3" /> {t('reports.data_time')}
                    </div>
                    <div className="font-mono text-indigo-700 font-semibold truncate">
                      {formatTime(report.metadata?.data_timestamp || report.time_range_end)}
                    </div>
                  </div>
                  <div className="border-l border-gray-200 pl-2">
                    <div className="font-medium text-gray-400 mb-0.5 flex items-center gap-1">
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
                <div className="text-sm text-gray-600 leading-relaxed font-serif line-clamp-4 max-h-24 overflow-hidden relative">
                  {(report.content || '').replace(/[#*`]/g, '')}
                  <div className="absolute bottom-0 left-0 w-full h-6 bg-gradient-to-t from-white to-transparent" />
                </div>
              </div>

              {/* Footer */}
              <div className="pl-3 pt-3 mt-3 border-t border-gray-100 flex justify-between items-center text-xs text-gray-400">
                <span>{t('reports.id_prefix')} {report.id}</span>
                {Array.isArray(report.metadata?.tags) && report.metadata.tags.length > 0 && (
                  <div className="flex gap-1">
                    {report.metadata.tags.slice(0, 2).map(tag => (
                      <span key={tag} className="bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">
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
            className="bg-white rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden shadow-2xl flex flex-col transform animate-in zoom-in-95 duration-200"
            onClick={e => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-start justify-between p-6 border-b border-gray-100 bg-white sticky top-0 z-10">
              <div>
                <div className="flex items-center space-x-3 mb-2">
                  <SourceBadge report={selectedReport} size="md" />
                  <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide border ${selectedReport.alert_level === 'critical' ? 'bg-red-50 text-red-700 border-red-100' :
                      selectedReport.alert_level === 'warning' ? 'bg-yellow-50 text-yellow-700 border-yellow-100' :
                        selectedReport.alert_level === 'info' ? 'bg-blue-50 text-blue-700 border-blue-100' :
                          'bg-green-50 text-green-700 border-green-100'
                    }`}>
                    {selectedReport.alert_level || 'normal'}
                  </span>
                  <span className="text-xs text-gray-400 uppercase tracking-widest font-semibold flex items-center">
                    <Calendar className="w-3 h-3 mr-1" />
                    {parseBackendDate(selectedReport.created_at).toLocaleString()}
                  </span>
                </div>
                <h2 className="text-2xl font-bold text-gray-900 leading-tight">
                  {selectedReport.title || t('reports.health_analysis_report')}
                </h2>
              </div>
              <button
                onClick={() => setSelectedReport(null)}
                className="p-2 hover:bg-gray-100 rounded-full transition-colors text-gray-400 hover:text-gray-600"
              >
                <X className="w-6 h-6" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-8 overflow-y-auto font-serif text-base leading-7 text-gray-800 bg-gray-50 selection:bg-indigo-100 selection:text-indigo-900">
              <div className="prose prose-indigo max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {selectedReport.content || ''}
                </ReactMarkdown>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="p-4 border-t border-gray-100 bg-white flex justify-between items-center text-xs text-gray-400">
              <div>
                {t('reports.report_id')} {selectedReport.id} • {t('reports.source')} {getReportSource(selectedReport) === 'quick_analysis' ? t('reports.source_quick') : t('reports.source_scheduled')} • {t('reports.data_time_label')} {selectedReport.metadata?.data_timestamp ? parseBackendDate(selectedReport.metadata.data_timestamp).toLocaleString() : t('common.na')}
              </div>
              <button
                onClick={() => setSelectedReport(null)}
                className="btn bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium px-6"
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
