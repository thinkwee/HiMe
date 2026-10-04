import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api.js'
import { parseBackendDate } from '../lib/utils'
import { useOnActivate } from '../lib/hooks'
import {
  Database,
  Wrench,
  Table,
  Hash,
  Calendar,
  RefreshCw,
  Search,
  Code,
  Terminal,
  Eye,
  EyeOff,
} from 'lucide-react'
import React from 'react'
import Skeleton from '../components/Skeleton'

export default function MemoryAndTools({ active = true }) {
  const { t } = useTranslation()
  const [memoryStats, setMemoryStats] = useState(null)
  const [tools, setTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('memory')

  // Expansion State
  const [expandedTable, setExpandedTable] = useState(null)
  // Mirrors expandedTable so in-flight inspect responses can be discarded
  // when they arrive after the user moved to another table.
  const expandedTableRef = useRef(null)
  const [tableData, setTableData] = useState([])
  const [inspectLoading, setInspectLoading] = useState(false)

  const fetchData = async () => {
    setLoading(true)
    setError(null)
    try {
      const [toolsRes, memoryRes] = await Promise.all([
        api.getTools(),
        api.queryAgentMemory('stats')
      ])

      if (toolsRes.success) setTools(toolsRes.tools)
      else setError(toolsRes.error || t('knowledge.failed_load_tools'))

      if (memoryRes.success) setMemoryStats(memoryRes.data)
      else setError(prev => prev || memoryRes.error || t('knowledge.failed_load_memory'))
    } catch (err) {
      console.error('Failed to fetch data:', err)
      setError(err.message || t('knowledge.failed_fetch'))
    } finally {
      setLoading(false)
    }
  }

  /** Load the rows of `tableName` into the expanded panel. */
  const inspectTable = async (tableName) => {
    setInspectLoading(true)
    setTableData([])
    setError(null)
    try {
      const res = await api.inspectMemoryTable(tableName)
      // A slower earlier request must not overwrite the table the user is
      // looking at now.
      if (expandedTableRef.current !== tableName) return
      if (res.success) {
        setTableData(Array.isArray(res.rows) ? res.rows : [])
      } else {
        setError(res.error || t('knowledge.failed_inspect', { name: tableName }))
      }
    } catch (err) {
      console.error('Failed to inspect table:', err)
      if (expandedTableRef.current !== tableName) return
      setError(err.message || t('knowledge.failed_inspect', { name: tableName }))
    } finally {
      if (expandedTableRef.current === tableName) setInspectLoading(false)
    }
  }

  // The page stays mounted while hidden, so refetch when its route becomes
  // active again (the agent writes to memory in the background).
  useOnActivate(active, () => {
    fetchData()
    if (expandedTableRef.current) inspectTable(expandedTableRef.current)
  })

  const handleRefresh = () => {
    fetchData()
    if (expandedTableRef.current) inspectTable(expandedTableRef.current)
  }

  // Load once on mount. Not memoized into the dependency array on purpose:
  // fetchData closes over `t`, so a language switch would re-run it and flash
  // the loading skeleton over data that is already correct.
  //
  // set-state-in-effect is a false positive here — the only synchronous updates
  // are setLoading(true)/setError(null), and `loading` already starts as true
  // and `error` as null, so React bails out of both and no render cascades.
  // Everything else happens after `await`. (The rule accepts the identical call
  // when it is wrapped in a local async function, which is why it fires here.)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchData()
  }, [])

  const toggleExpand = async (tableName) => {
    if (expandedTable === tableName) {
      setExpandedTable(null)
      expandedTableRef.current = null
      return
    }

    setExpandedTable(tableName)
    expandedTableRef.current = tableName
    await inspectTable(tableName)
  }

  return (
    <div className="space-y-6 animate-fade-in relative">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 className="page-title">{t('knowledge.title')}</h2>

        <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading}
          aria-label={t('common.refresh')}
          title={t('common.refresh')}
          className="p-2 rounded-control border border-line bg-panel text-ink-2 hover:text-ink hover:bg-sunken disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <div className="flex items-center space-x-2 bg-sunken p-1 rounded-card border border-line">
          <button
            type="button"
            aria-pressed={activeTab === 'memory'}
            onClick={() => setActiveTab('memory')}
            className={`px-5 py-1.5 rounded-control text-xs font-bold transition-all flex items-center gap-2 ${activeTab === 'memory' ? 'bg-panel text-ink shadow-sm' : 'text-ink-2 hover:bg-sunken'
              }`}
          >
            <Database className="w-3.5 h-3.5" /> {t('knowledge.tab_memory')}
          </button>
          <button
            type="button"
            aria-pressed={activeTab === 'tools'}
            onClick={() => setActiveTab('tools')}
            className={`px-5 py-1.5 rounded-control text-xs font-bold transition-all flex items-center gap-2 ${activeTab === 'tools' ? 'bg-panel text-ink shadow-sm' : 'text-ink-2 hover:bg-sunken'
              }`}
          >
            <Wrench className="w-3.5 h-3.5" /> {t('knowledge.tab_tools')}
          </button>
        </div>
        </div>
      </div>

      {error && (
        <div role="alert" className="bg-bad/10 border border-bad/30 rounded-card px-4 py-3 text-sm text-bad-ink font-medium flex items-center gap-3">
          <span className="flex-1 break-words">{error}</span>
          <button type="button" onClick={handleRefresh} className="underline font-semibold">{t('common.retry')}</button>
        </div>
      )}

      <div className="space-y-6 p-1">
        <div className="min-w-0">
          {loading ? (
            <Skeleton lines={5} label={t('knowledge.synchronizing')} />
          ) : activeTab === 'tools' ? (
            /* Tools List */
            <div className="max-h-[calc(100vh-250px)] overflow-y-auto pr-2 space-y-4 custom-scrollbar">
              {tools.map((tool) => (
                <div key={tool.function.name} className="card shadow-none border-line border-l-4 border-l-line hover:border-l-primary-500 group">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-sunken text-ink-2 rounded-control group-hover:bg-primary-50 group-hover:text-primary-600 transition-colors">
                        {tool.function.name === 'sql' ? <Search className="w-4 h-4" /> :
                          tool.function.name === 'code' ? <Code className="w-4 h-4" /> :
                              <Terminal className="w-4 h-4" />}
                      </div>
                      <h3 className="font-bold text-lg text-ink">{tool.function.name}</h3>
                    </div>
                  </div>
                  <p className="text-sm text-ink-2 leading-relaxed mb-4 font-medium">
                    {tool.function.description}
                  </p>

                  <div className="bg-sunken/50 rounded-card p-3 border border-line">
                    <div className="text-[10px] font-bold text-ink-3 uppercase tracking-widest mb-2">{t('knowledge.parameters')}</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1">
                      {Object.entries(tool.function.parameters.properties).map(([name, schema]) => (
                        <div key={name} className="flex items-center justify-between font-mono text-[11px] py-1 border-b border-line last:border-0">
                          <span className="text-ink font-bold">{name}{tool.function.parameters.required?.includes(name) ? '*' : ''}</span>
                          <span className="text-ink-3">{schema.type}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* Memory Stats View with Inline Expansion */
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="card shadow-none border-line p-4 flex items-center gap-4">
                  <div className="p-3 bg-info/10 text-info rounded-card"><Table className="w-5 h-5" /></div>
                  <div>
                    <div className="text-[10px] font-bold text-ink-3 uppercase tracking-tighter">{t('knowledge.tables')}</div>
                    <div className="text-2xl font-bold text-ink">{Object.keys(memoryStats?.table_counts || {}).length}</div>
                  </div>
                </div>
                <div className="card shadow-none border-line p-4 flex items-center gap-4">
                  <div className="p-3 bg-info/10 text-info rounded-card"><Hash className="w-5 h-5" /></div>
                  <div>
                    <div className="text-[10px] font-bold text-ink-3 uppercase tracking-tighter">{t('knowledge.total_rows')}</div>
                    <div className="text-2xl font-bold text-ink">{Object.values(memoryStats?.table_counts || {}).reduce((a, b) => a + (b > 0 ? b : 0), 0).toLocaleString()}</div>
                  </div>
                </div>
                <div className="card shadow-none border-line p-4 flex items-center gap-4">
                  <div className="p-3 bg-ok/10 text-ok rounded-card"><Calendar className="w-5 h-5" /></div>
                  <div>
                    <div className="text-[10px] font-bold text-ink-3 uppercase tracking-tighter">{t('knowledge.activity')}</div>
                    <div className="text-sm font-bold text-ink truncate">{memoryStats?.date_range?.max ? parseBackendDate(memoryStats.date_range.max).toLocaleDateString() : t('knowledge.none')}</div>
                  </div>
                </div>
              </div>

              {/* Outer card uses overflow-hidden so inner content cannot stretch it wider */}
              <div className="card p-0 overflow-hidden border-line shadow-none">
                {/* Outer table area scrolls horizontally but width is clamped inside the card */}
                <div className="overflow-x-auto">
                  <table className="w-full text-left" style={{ tableLayout: 'fixed', minWidth: '500px' }}>
                    <colgroup>
                      <col style={{ width: '35%' }} />
                      <col style={{ width: '15%' }} />
                      <col style={{ width: '20%' }} />
                      <col style={{ width: '30%' }} />
                    </colgroup>
                    <thead>
                      <tr className="border-b border-line bg-sunken/50">
                        <th className="px-6 py-4 text-[10px] font-bold text-ink-3 uppercase tracking-widest">{t('knowledge.col_table_name')}</th>
                        <th className="px-6 py-4 text-[10px] font-bold text-ink-3 uppercase tracking-widest">{t('knowledge.col_rows')}</th>
                        <th className="px-6 py-4 text-[10px] font-bold text-ink-3 uppercase tracking-widest">{t('knowledge.col_type')}</th>
                        <th className="px-6 py-4 text-[10px] font-bold text-ink-3 uppercase tracking-widest">{t('knowledge.col_actions')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {Object.entries(memoryStats?.table_counts || {}).sort(([a], [b]) => a.localeCompare(b)).map(([name, count]) => {
                        const isSystem = ['reports', 'activity_log'].includes(name)
                        const isExpanded = expandedTable === name
                        return (
                          <React.Fragment key={name}>
                            <tr className={`transition-colors group ${isExpanded ? 'bg-primary-50/30' : 'hover:bg-sunken/50'}`}>
                              <td className="px-6 py-4">
                                <span className="font-mono font-bold text-ink text-sm">{name}</span>
                              </td>
                              <td className="px-6 py-4">
                                <span className="font-bold text-ink-2 text-sm">{count >= 0 ? count.toLocaleString() : t('knowledge.row_error')}</span>
                              </td>
                              <td className="px-6 py-4">
                                <span className={`text-[10px] font-bold uppercase tracking-tighter ${isSystem ? 'text-info-ink bg-info/15' : 'text-primary-700 bg-primary-100'} px-2 py-1 rounded-chip`}>
                                  {isSystem ? t('knowledge.type_system') : t('knowledge.type_agent')}
                                </span>
                              </td>
                              <td className="px-6 py-4">
                                <button
                                  type="button"
                                  aria-expanded={isExpanded}
                                  onClick={() => toggleExpand(name)}
                                  className={`flex items-center gap-1.5 text-xs font-bold transition-all ${isExpanded ? 'text-primary-700' : 'text-primary-600'
                                    }`}
                                >
                                  {isExpanded ? (
                                    <><EyeOff className="w-3.5 h-3.5" /> {t('knowledge.collapse')}</>
                                  ) : (
                                    <><Eye className="w-3.5 h-3.5" /> {t('knowledge.inspect')}</>
                                  )}
                                </button>
                              </td>
                            </tr>

                            {/* Expanded detail row: colspan fills the width, but its content area is independent and does not affect the outer table's column widths */}
                            {isExpanded && (
                              <tr>
                                <td colSpan="4" className="p-0 border-b border-line">
                                  <div className="bg-panel p-4 font-mono">
                                    {inspectLoading ? (
                                      <div className="flex items-center justify-center py-12 gap-3">
                                        <RefreshCw className="w-5 h-5 text-primary-500 animate-spin" />
                                        <span className="text-xs font-bold text-ink-3 uppercase">{t('knowledge.fetching')}</span>
                                      </div>
                                    ) : tableData.length > 0 ? (
                                      /*
                                        Key fix:
                                        1. Outer div uses overflow-x-auto + max-h; the inner table is free to stretch.
                                        2. Inner table does NOT set table-layout: fixed, so column widths adapt to content.
                                        3. Cells use whitespace-normal + break-all so long content wraps instead of
                                           stretching the row; max-w-xs caps any single column, and horizontal scroll
                                           handles the many-column case.
                                      */
                                      <div className="overflow-x-auto max-h-[400px] border border-line rounded-card custom-scrollbar">
                                        <table className="text-left text-[11px] border-collapse" style={{ minWidth: '100%' }}>
                                          <thead className="bg-sunken sticky top-0 font-bold text-ink-3">
                                            <tr>
                                              {Object.keys(tableData[0] || {}).map(k => (
                                                <th
                                                  key={k}
                                                  className="px-3 py-2 border-b border-line uppercase bg-sunken whitespace-nowrap"
                                                  style={{ maxWidth: '240px', minWidth: '80px' }}
                                                >
                                                  {k}
                                                </th>
                                              ))}
                                            </tr>
                                          </thead>
                                          <tbody className="divide-y divide-line">
                                            {tableData.map((row, i) => (
                                              <tr key={i} className="hover:bg-sunken transition-colors">
                                                {Object.values(row).map((v, j) => (
                                                  <td
                                                    key={j}
                                                    className="px-3 py-2 text-ink-2 align-top"
                                                    style={{ maxWidth: '240px', wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}
                                                  >
                                                    {v === null ? (
                                                      <span className="text-ink-3">—</span>
                                                    ) : typeof v === 'object' ? (
                                                      JSON.stringify(v)
                                                    ) : (
                                                      String(v)
                                                    )}
                                                  </td>
                                                ))}
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      </div>
                                    ) : (
                                      <div className="py-12 text-center text-ink-3 font-bold text-xs uppercase">{t('knowledge.no_records')}</div>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}