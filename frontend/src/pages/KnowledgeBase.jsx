import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api.js'
import { parseBackendDate } from '../lib/utils'
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

export default function MemoryAndTools() {
  const { t } = useTranslation()
  const [memoryStats, setMemoryStats] = useState(null)
  const [tools, setTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('memory')

  // Expansion State
  const [expandedTable, setExpandedTable] = useState(null)
  const [tableData, setTableData] = useState([])
  const [inspectLoading, setInspectLoading] = useState(false)

  useEffect(() => {
    fetchData()
  }, [])

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

  const toggleExpand = async (tableName) => {
    if (expandedTable === tableName) {
      setExpandedTable(null)
      return
    }

    setExpandedTable(tableName)
    setInspectLoading(true)
    setTableData([])
    setError(null)
    try {
      const res = await api.inspectMemoryTable(tableName)
      if (res.success) {
        setTableData(res.rows)
      } else {
        setError(res.error || t('knowledge.failed_inspect', { name: tableName }))
      }
    } catch (err) {
      console.error('Failed to inspect table:', err)
      setError(err.message || t('knowledge.failed_inspect', { name: tableName }))
    } finally {
      setInspectLoading(false)
    }
  }

  return (
    <div className="space-y-6 animate-fade-in relative">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-3xl font-black text-gray-900 tracking-tight">{t('knowledge.title')}</h2>

        <div className="flex items-center space-x-2 bg-gray-100 p-1 rounded-xl border border-gray-200">
          <button
            onClick={() => setActiveTab('memory')}
            className={`px-5 py-1.5 rounded-lg text-xs font-black transition-all flex items-center gap-2 ${activeTab === 'memory' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:bg-gray-50'
              }`}
          >
            <Database className="w-3.5 h-3.5" /> {t('knowledge.tab_memory')}
          </button>
          <button
            onClick={() => setActiveTab('tools')}
            className={`px-5 py-1.5 rounded-lg text-xs font-black transition-all flex items-center gap-2 ${activeTab === 'tools' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:bg-gray-50'
              }`}
          >
            <Wrench className="w-3.5 h-3.5" /> {t('knowledge.tab_tools')}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700 font-medium">
          {error}
        </div>
      )}

      <div className="space-y-6 p-1">
        <div className="min-w-0">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-24 bg-white rounded-3xl border border-gray-100">
              <RefreshCw className="w-8 h-8 text-primary-500 animate-spin mb-4" />
              <p className="font-black text-gray-400 text-xs uppercase tracking-widest">{t('knowledge.synchronizing')}</p>
            </div>
          ) : activeTab === 'tools' ? (
            /* Tools List */
            <div className="max-h-[calc(100vh-250px)] overflow-y-auto pr-2 space-y-4 custom-scrollbar">
              {tools.map((tool) => (
                <div key={tool.function.name} className="card shadow-none border-gray-200 border-l-4 border-l-gray-200 hover:border-l-primary-500 group">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-gray-50 text-gray-500 rounded-lg group-hover:bg-primary-50 group-hover:text-primary-600 transition-colors">
                        {tool.function.name === 'sql' ? <Search className="w-4 h-4" /> :
                          tool.function.name === 'code' ? <Code className="w-4 h-4" /> :
                              <Terminal className="w-4 h-4" />}
                      </div>
                      <h3 className="font-black text-lg text-gray-900">{tool.function.name}</h3>
                    </div>
                  </div>
                  <p className="text-sm text-gray-600 leading-relaxed mb-4 font-medium">
                    {tool.function.description}
                  </p>

                  <div className="bg-gray-50/50 rounded-xl p-3 border border-gray-100">
                    <div className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2">{t('knowledge.parameters')}</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1">
                      {Object.entries(tool.function.parameters.properties).map(([name, schema]) => (
                        <div key={name} className="flex items-center justify-between font-mono text-[11px] py-1 border-b border-gray-100 last:border-0">
                          <span className="text-gray-700 font-bold">{name}{tool.function.parameters.required?.includes(name) ? '*' : ''}</span>
                          <span className="text-gray-400">{schema.type}</span>
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
                <div className="card shadow-none border-gray-200 p-4 flex items-center gap-4">
                  <div className="p-3 bg-blue-50 text-blue-500 rounded-xl"><Table className="w-5 h-5" /></div>
                  <div>
                    <div className="text-[10px] font-black text-gray-400 uppercase tracking-tighter">{t('knowledge.tables')}</div>
                    <div className="text-2xl font-black text-gray-900">{Object.keys(memoryStats?.table_counts || {}).length}</div>
                  </div>
                </div>
                <div className="card shadow-none border-gray-200 p-4 flex items-center gap-4">
                  <div className="p-3 bg-purple-50 text-purple-500 rounded-xl"><Hash className="w-5 h-5" /></div>
                  <div>
                    <div className="text-[10px] font-black text-gray-400 uppercase tracking-tighter">{t('knowledge.total_rows')}</div>
                    <div className="text-2xl font-black text-gray-900">{Object.values(memoryStats?.table_counts || {}).reduce((a, b) => a + (b > 0 ? b : 0), 0).toLocaleString()}</div>
                  </div>
                </div>
                <div className="card shadow-none border-gray-200 p-4 flex items-center gap-4">
                  <div className="p-3 bg-emerald-50 text-emerald-500 rounded-xl"><Calendar className="w-5 h-5" /></div>
                  <div>
                    <div className="text-[10px] font-black text-gray-400 uppercase tracking-tighter">{t('knowledge.activity')}</div>
                    <div className="text-sm font-black text-gray-900 truncate">{memoryStats?.date_range?.max ? parseBackendDate(memoryStats.date_range.max).toLocaleDateString() : t('knowledge.none')}</div>
                  </div>
                </div>
              </div>

              {/* Outer card uses overflow-hidden so inner content cannot stretch it wider */}
              <div className="card p-0 overflow-hidden border-gray-200 shadow-none">
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
                      <tr className="border-b border-gray-100 bg-gray-50/50">
                        <th className="px-6 py-4 text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('knowledge.col_table_name')}</th>
                        <th className="px-6 py-4 text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('knowledge.col_rows')}</th>
                        <th className="px-6 py-4 text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('knowledge.col_type')}</th>
                        <th className="px-6 py-4 text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('knowledge.col_actions')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {Object.entries(memoryStats?.table_counts || {}).sort(([a], [b]) => a.localeCompare(b)).map(([name, count]) => {
                        const isSystem = ['reports', 'activity_log'].includes(name)
                        const isExpanded = expandedTable === name
                        return (
                          <React.Fragment key={name}>
                            <tr className={`transition-colors group ${isExpanded ? 'bg-primary-50/30' : 'hover:bg-gray-50/50'}`}>
                              <td className="px-6 py-4">
                                <span className="font-mono font-black text-gray-900 text-sm">{name}</span>
                              </td>
                              <td className="px-6 py-4">
                                <span className="font-black text-gray-500 text-sm">{count >= 0 ? count.toLocaleString() : t('knowledge.row_error')}</span>
                              </td>
                              <td className="px-6 py-4">
                                <span className={`text-[10px] font-black uppercase tracking-tighter ${isSystem ? 'text-blue-500 bg-blue-50 px-2 py-1 rounded' : 'text-purple-500 bg-purple-50 px-2 py-1 rounded'}`}>
                                  {isSystem ? t('knowledge.type_system') : t('knowledge.type_agent')}
                                </span>
                              </td>
                              <td className="px-6 py-4">
                                <button
                                  onClick={() => toggleExpand(name)}
                                  className={`flex items-center gap-1.5 text-xs font-black transition-all ${isExpanded ? 'text-primary-700' : 'text-primary-600'
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
                                <td colSpan="4" className="p-0 border-b border-gray-200">
                                  <div className="bg-white p-4 font-mono">
                                    {inspectLoading ? (
                                      <div className="flex items-center justify-center py-12 gap-3">
                                        <RefreshCw className="w-5 h-5 text-primary-500 animate-spin" />
                                        <span className="text-xs font-black text-gray-400 uppercase">{t('knowledge.fetching')}</span>
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
                                      <div className="overflow-x-auto max-h-[400px] border border-gray-100 rounded-xl custom-scrollbar">
                                        <table className="text-left text-[11px] border-collapse" style={{ minWidth: '100%' }}>
                                          <thead className="bg-gray-50 sticky top-0 font-black text-gray-400">
                                            <tr>
                                              {Object.keys(tableData[0]).map(k => (
                                                <th
                                                  key={k}
                                                  className="px-3 py-2 border-b border-gray-100 uppercase bg-gray-50 whitespace-nowrap"
                                                  style={{ maxWidth: '240px', minWidth: '80px' }}
                                                >
                                                  {k}
                                                </th>
                                              ))}
                                            </tr>
                                          </thead>
                                          <tbody className="divide-y divide-gray-50">
                                            {tableData.map((row, i) => (
                                              <tr key={i} className="hover:bg-gray-50 transition-colors">
                                                {Object.values(row).map((v, j) => (
                                                  <td
                                                    key={j}
                                                    className="px-3 py-2 text-gray-600 align-top"
                                                    style={{ maxWidth: '240px', wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}
                                                  >
                                                    {v === null ? (
                                                      <span className="text-gray-300">—</span>
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
                                      <div className="py-12 text-center text-gray-400 font-black text-xs uppercase">{t('knowledge.no_records')}</div>
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

      <style>{`
        .custom-scrollbar::-webkit-scrollbar {
          width: 6px;
          height: 6px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: transparent;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: #e5e7eb;
          border-radius: 10px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: #d1d5db;
        }
      `}</style>
    </div>
  )
}