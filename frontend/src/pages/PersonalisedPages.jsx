import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import {
  AppWindow, Trash2, ExternalLink, RefreshCw, X, Maximize2, Minimize2,
} from 'lucide-react'
import { parseBackendDate } from '../lib/utils'

export default function PersonalisedPages() {
  const { t } = useTranslation()
  const [pages, setPages] = useState([])
  const [loading, setLoading] = useState(true)
  const [activePage, setActivePage] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const [deleting, setDeleting] = useState(null)
  const iframeRef = useRef(null)

  const loadPages = async () => {
    setLoading(true)
    const res = await api.listPersonalisedPages()
    if (res.success) setPages(res.pages || [])
    setLoading(false)
  }

  useEffect(() => { loadPages() }, [])

  const handleDelete = async (pageId) => {
    if (!confirm(t('pages.confirm_delete', { pageId }))) return
    setDeleting(pageId)
    try {
      const res = await api.deletePersonalisedPage(pageId)
      if (res.success) {
        setPages((prev) => prev.filter((a) => a.page_id !== pageId))
        if (activePage?.page_id === pageId) setActivePage(null)
      } else {
        console.error('Delete failed:', res.error)
        alert(t('pages.delete_failed', { error: res.error || t('common.unknown_error') }))
      }
    } catch (err) {
      console.error('Delete page error:', err)
      alert(t('pages.delete_failed', { error: err.message || t('common.network_error') }))
    }
    setDeleting(null)
  }

  const openPage = (page) => {
    setActivePage(page)
    setExpanded(false)
  }

  const pageUrl = (pageId) => `/api/personalised-pages/${pageId}/`

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <AppWindow className="w-8 h-8 text-primary-600" />
          <h2 className="text-3xl font-bold text-gray-900">{t('pages.title')}</h2>
          <span className="text-sm text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">
            {pages.length}
          </span>
        </div>
        <button onClick={loadPages} className="btn btn-secondary flex items-center space-x-2">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          <span>{t('pages.refresh')}</span>
        </button>
      </div>

      {/* Page list */}
      {loading && pages.length === 0 ? (
        <div className="card p-12 text-center text-gray-500">{t('pages.loading')}</div>
      ) : pages.length === 0 ? (
        <div className="card p-12 text-center">
          <AppWindow className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500">{t('pages.empty')}</p>
          <p className="text-sm text-gray-400 mt-1">
            {t('pages.empty_hint')}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {pages.map((page) => (
            <div
              key={page.page_id}
              className={`card p-4 cursor-pointer transition-all hover:shadow-md ${
                activePage?.page_id === page.page_id
                  ? 'ring-2 ring-primary-500 shadow-md'
                  : ''
              }`}
              onClick={() => openPage(page)}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-gray-900 truncate">
                    {page.display_name}
                  </h3>
                  <p className="text-xs text-gray-400 font-mono mt-0.5">{page.page_id}</p>
                </div>
                <div className="flex items-center space-x-1 ml-2 flex-shrink-0">
                  <a
                    href={pageUrl(page.page_id)}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 text-gray-400 hover:text-primary-600 rounded-lg hover:bg-gray-100"
                    title={t('pages.open_new_tab')}
                  >
                    <ExternalLink className="w-4 h-4" />
                  </a>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(page.page_id) }}
                    disabled={deleting === page.page_id}
                    className="p-1.5 text-gray-400 hover:text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50"
                    title={t('pages.delete_page')}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
              {page.description && (
                <p className="text-sm text-gray-500 mt-2 line-clamp-2">{page.description}</p>
              )}
              {page.created_at && (
                <p className="text-xs text-gray-400 mt-2">
                  {parseBackendDate(page.created_at).toLocaleString()}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Embedded page viewer */}
      {activePage && (
        <div className={`card overflow-hidden ${expanded ? 'fixed inset-4 z-50 m-0' : ''}`}>
          {/* Toolbar */}
          <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
            <div className="flex items-center space-x-2 min-w-0">
              <AppWindow className="w-4 h-4 text-primary-600 flex-shrink-0" />
              <span className="font-medium text-sm text-gray-700 truncate">
                {activePage.display_name}
              </span>
            </div>
            <div className="flex items-center space-x-1">
              <a
                href={pageUrl(activePage.page_id)}
                target="_blank"
                rel="noopener noreferrer"
                className="p-1.5 text-gray-400 hover:text-primary-600 rounded"
                title={t('pages.open_new_tab')}
              >
                <ExternalLink className="w-4 h-4" />
              </a>
              <button
                onClick={() => setExpanded(!expanded)}
                className="p-1.5 text-gray-400 hover:text-gray-600 rounded"
                title={expanded ? t('pages.minimize') : t('pages.maximize')}
              >
                {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
              </button>
              <button
                onClick={() => { setActivePage(null); setExpanded(false) }}
                className="p-1.5 text-gray-400 hover:text-gray-600 rounded"
                title={t('pages.close')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
          {/* iframe */}
          <iframe
            ref={iframeRef}
            src={pageUrl(activePage.page_id)}
            className={`w-full border-0 ${expanded ? 'flex-1' : ''}`}
            style={{ height: expanded ? 'calc(100% - 41px)' : '600px' }}
            title={activePage.display_name}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          />
        </div>
      )}

      {/* Fullscreen backdrop */}
      {expanded && (
        <div
          className="fixed inset-0 bg-black/30 z-40"
          onClick={() => setExpanded(false)}
        />
      )}
    </div>
  )
}
