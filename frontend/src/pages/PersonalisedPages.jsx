import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { api, getAuthToken } from '../lib/api'
import {
  AppWindow, Trash2, ExternalLink, RefreshCw, X, Maximize2, Minimize2,
} from 'lucide-react'
import { parseBackendDate } from '../lib/utils'

// ---------------------------------------------------------------------------
// Why the page HTML is fetched here instead of being an `<iframe src>`
// ---------------------------------------------------------------------------
// `/api/personalised-pages/{id}/` sits behind the API bearer token like every
// other /api route, and a frame navigation cannot carry an Authorization
// header. The only URL-based option is `?token=…`, and that is exactly the
// wrong trade here: the document in the frame is agent-generated code that the
// sandbox below exists to contain, and it can read its own `location.search`.
// A page that got prompt-injected through health data or chat content would
// come away with the master, non-expiring API credential — plus the token
// would be written into browser history and every proxy log on the way.
//
// So the parent fetches the markup with the header (api.getPersonalisedPageHtml)
// and hands it to the frame through `srcdoc`. The frame's URL is `about:srcdoc`:
// there is no query string, no referrer, and nothing for the page to read.
//
// The catch is that a `srcdoc` document is not an HTTP response, so the
// Content-Security-Policy header the backend sets on this route does not reach
// it. `page_routes.py` therefore also emits the policy as a
// `<meta http-equiv="Content-Security-Policy">` inside the markup, which is
// what actually constrains the document here. That works only because the same
// module already inlines the shared CSS/JS (it had to, for WebKit) — a srcdoc
// page in an opaque origin can load no same-origin subresource at all.

// ---------------------------------------------------------------------------
// Sandboxed-page data bridge
// ---------------------------------------------------------------------------
// The iframe below is `sandbox="allow-scripts"` — deliberately WITHOUT
// `allow-same-origin`, which the HTML spec says would neuter the sandbox
// entirely. The page therefore runs in an opaque origin and can reach neither
// `window.parent`, this app's localStorage, nor any `/api/*` endpoint.
//
// The cost is that its own data endpoint is cross-origin to it as well
// (`Origin: null`), so it cannot fetch it. Instead the page postMessages a
// request up here and we perform the fetch for it. That only stays safe if the
// child cannot influence WHAT we fetch, so the rules below are load-bearing:
//
//   * the path is built from the page currently open in React state — never
//     from anything in the message;
//   * the method is restricted to GET/POST;
//   * query params and the POST body are shape-checked and size-capped. They
//     are forwarded because they only ever reach *this page's own*
//     `route_handler`, which the page already fully controls — no other
//     endpoint is reachable, so this grants the child no new authority.
//
// Responses go back with targetOrigin '*' (an opaque origin cannot be named),
// so they must never carry anything beyond that page's own data payload.

const MAX_REQUEST_ID_LEN = 64
const MAX_PARAM_COUNT = 8
const MAX_PARAM_KEY_LEN = 32
const MAX_PARAM_VALUE_LEN = 256
const MAX_BODY_BYTES = 64 * 1024
const PARAM_KEY_RE = /^[A-Za-z0-9_-]{1,32}$/

/** Whitelist query params to simple key/value strings. Returns null if invalid. */
function sanitiseParams(raw) {
  if (raw === null || raw === undefined) return {}
  if (typeof raw !== 'object' || Array.isArray(raw)) return null
  const keys = Object.keys(raw)
  if (keys.length > MAX_PARAM_COUNT) return null
  const out = {}
  for (const k of keys) {
    if (k.length > MAX_PARAM_KEY_LEN || !PARAM_KEY_RE.test(k)) return null
    const v = raw[k]
    if (v === null || v === undefined) continue
    if (typeof v === 'object') return null
    const s = String(v)
    if (s.length > MAX_PARAM_VALUE_LEN) return null
    out[k] = s
  }
  return out
}

/** Serialise a POST body, rejecting non-objects and oversized payloads. */
function sanitiseBody(raw) {
  const value = raw === null || raw === undefined ? {} : raw
  if (typeof value !== 'object' || Array.isArray(value)) return null
  let json
  try {
    json = JSON.stringify(value)
  } catch {
    return null
  }
  if (json === undefined || json.length > MAX_BODY_BYTES) return null
  return json
}

export default function PersonalisedPages() {
  const { t } = useTranslation()
  const [pages, setPages] = useState([])
  const [loading, setLoading] = useState(true)
  const [activePage, setActivePage] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const [deleting, setDeleting] = useState(null)
  // Fetched document for the open page: { pageId, html } or { pageId, error }.
  // Kept as one object (rather than separate html/error/loading flags) so the
  // page id it belongs to is part of the value — the viewer below renders it
  // only while it still matches the open page, which means switching pages
  // never needs a synchronous state reset inside an effect.
  const [pageDoc, setPageDoc] = useState(null)
  const iframeRef = useRef(null)

  const loadPages = async () => {
    setLoading(true)
    const res = await api.listPersonalisedPages()
    if (res.success) setPages(res.pages || [])
    setLoading(false)
  }

  // Initial load. The state updates are deliberately kept behind the `await`
  // (rather than calling loadPages(), which sets `loading` synchronously) so
  // the effect never sets state during render — react-hooks/set-state-in-effect.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const res = await api.listPersonalisedPages()
      if (cancelled) return
      const list = res.success ? (res.pages || []) : []
      setPages(list)
      // `?page=<id>` — what the "open in a new tab" buttons link to. The new
      // tab is this same SPA, so the page still renders inside the sandboxed
      // frame below instead of running top-level with the app's origin.
      const wanted = new URLSearchParams(window.location.search).get('page')
      const match = wanted ? list.find((p) => p.page_id === wanted) : null
      if (match) {
        setActivePage(match)
        setExpanded(true)
      }
      setLoading(false)
    })()
    return () => { cancelled = true }
  }, [])

  // Fetch the open page's HTML with the bearer token and render it via
  // `srcdoc` — see the block comment at the top of this file for why it is not
  // an `<iframe src>`.
  const activePageIdForDoc = activePage?.page_id
  useEffect(() => {
    if (!activePageIdForDoc) return undefined
    let cancelled = false
    ;(async () => {
      const res = await api.getPersonalisedPageHtml(activePageIdForDoc)
      if (cancelled) return
      // The error is kept untranslated here so a language switch does not
      // change this effect's identity and re-fetch the whole document.
      setPageDoc(
        res.success
          ? { pageId: activePageIdForDoc, html: res.html }
          : { pageId: activePageIdForDoc, error: res.error || '' },
      )
    })()
    return () => { cancelled = true }
  }, [activePageIdForDoc])

  // Data bridge for the sandboxed page iframe — see the block comment above.
  const activePageId = activePage?.page_id
  useEffect(() => {
    if (!activePageId) return undefined

    const reply = (target, requestId, msg) => {
      // Re-check the target: an await elapsed, and the user may have closed or
      // switched pages. Only ever answer the iframe still mounted right now.
      if (!iframeRef.current || iframeRef.current.contentWindow !== target) return
      // targetOrigin must be '*' — the sandboxed child has an opaque origin
      // and cannot be named. Nothing but this page's own payload is sent.
      // Envelope fields last so a payload key can never shadow them.
      target.postMessage({ ...msg, type: 'hime:data-response', requestId }, '*')
    }

    const onMessage = async (event) => {
      // 1. The message must come from the page iframe we are currently showing.
      const frame = iframeRef.current
      if (!frame || !event.source || event.source !== frame.contentWindow) return

      const msg = event.data
      if (!msg || typeof msg !== 'object' || msg.type !== 'hime:data-request') return

      // 2. Correlation id — echoed back verbatim, so bound it.
      const requestId = msg.requestId
      if (typeof requestId !== 'string' || !requestId || requestId.length > MAX_REQUEST_ID_LEN) return

      const src = event.source

      // 3. Method whitelist.
      const method = msg.method === 'POST' ? 'POST' : msg.method === 'GET' ? 'GET' : null
      if (!method) {
        reply(src, requestId, { ok: false, error: 'Unsupported method' })
        return
      }

      // 4. The page id comes from React state, NOT from the message. The
      //    message's own pageId is only used to reject stale/confused requests.
      if (msg.pageId !== undefined && msg.pageId !== null && msg.pageId !== activePageId) {
        reply(src, requestId, { ok: false, error: 'Page mismatch' })
        return
      }
      const url = `/api/personalised-pages/${encodeURIComponent(activePageId)}/data`

      // Resolve the token per request, exactly like lib/api.js does: it lives
      // in session/localStorage (whatever <AuthTokenPrompt> stored) and is not
      // known at build time, so a module-level constant would be empty in every
      // Docker install and every proxied request would 401. It stays on this
      // side of the bridge — only the JSON response crosses back.
      const token = getAuthToken()
      const headers = token ? { Authorization: `Bearer ${token}` } : {}

      let request
      if (method === 'POST') {
        const body = sanitiseBody(msg.body)
        if (body === null) {
          reply(src, requestId, { ok: false, error: 'Invalid request body' })
          return
        }
        request = [url, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body }]
      } else {
        const params = sanitiseParams(msg.params)
        if (params === null) {
          reply(src, requestId, { ok: false, error: 'Invalid query parameters' })
          return
        }
        const qs = new URLSearchParams(params).toString()
        request = [qs ? `${url}?${qs}` : url, { method: 'GET', headers }]
      }

      try {
        const res = await fetch(...request)
        const payload = await res.json().catch(() => null)
        if (!res.ok) {
          reply(src, requestId, {
            ok: false,
            status: res.status,
            error: payload?.detail || payload?.error || `HTTP ${res.status}`,
          })
          return
        }
        reply(src, requestId, { ok: true, status: res.status, payload })
      } catch (err) {
        reply(src, requestId, { ok: false, error: err?.message || 'Network error' })
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [activePageId])

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

  // "Open in a new tab" opens THIS app at /pages?page=<id>, not the raw
  // /api/personalised-pages/<id>/ URL.
  //
  // Two reasons. The API URL is behind the bearer token, so a plain link 401s
  // unless the token is pasted into the query string — the leak described at
  // the top of this file, made worse by landing in browser history. And a
  // top-level load of that URL runs agent-generated HTML *unsandboxed* on the
  // app's own origin, with localStorage and every /api route at its disposal:
  // the one place the containment below did not reach. Re-entering through the
  // SPA gets both properties back — the new tab renders the page in the same
  // sandboxed frame, with the same bridge.
  //
  // window.open rather than an <a href target="_blank"> so the new tab is
  // opened as a same-origin dependent context and inherits a copy of this
  // tab's sessionStorage — which is where <AuthTokenPrompt> puts the token by
  // default. With rel="noopener" it would not, and the user would be asked for
  // the token again in every tab.
  const openInNewTab = (pageId) => {
    window.open(`/pages?page=${encodeURIComponent(pageId)}`, '_blank')
  }

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
                  <button
                    onClick={(e) => { e.stopPropagation(); openInNewTab(page.page_id) }}
                    className="p-1.5 text-gray-400 hover:text-primary-600 rounded-lg hover:bg-gray-100"
                    title={t('pages.open_new_tab')}
                  >
                    <ExternalLink className="w-4 h-4" />
                  </button>
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
              <button
                onClick={() => openInNewTab(activePage.page_id)}
                className="p-1.5 text-gray-400 hover:text-primary-600 rounded"
                title={t('pages.open_new_tab')}
              >
                <ExternalLink className="w-4 h-4" />
              </button>
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
          {/*
            `allow-scripts` ONLY — and that is the whole point.

            Adding `allow-same-origin` alongside `allow-scripts` lets the frame
            drop its own sandbox (HTML spec), which would give agent-generated
            JS this app's origin: window.parent, localStorage, and every
            /api/* endpoint with the user's credentials. Since the agent can be
            prompt-injected through health data or chat content and then emit a
            page via create_page, that is a real path to data loss.

            Without it the page is an opaque origin and truly isolated. It gets
            its data through the validated postMessage bridge above instead of
            fetching it itself.

            `allow-forms` is not granted either: HimeUI.InputForm submits over
            the bridge, not via a real form navigation. `allow-popups` is not
            granted because window.open() to an external URL is the one
            exfiltration path CSP cannot close.

            The CSP (connect-src/form-action 'self', base-uri 'none') is
            defence in depth rather than the only containment. It rides along
            inside the markup as a <meta http-equiv>, because srcdoc documents
            get no response headers — see page_routes._PAGE_CSP_META.
          */}
          {pageDoc?.pageId === activePage.page_id && pageDoc.html ? (
            <iframe
              ref={iframeRef}
              key={activePage.page_id}
              srcDoc={pageDoc.html}
              className={`w-full border-0 ${expanded ? 'flex-1' : ''}`}
              style={{ height: expanded ? 'calc(100% - 41px)' : '600px' }}
              title={activePage.display_name}
              sandbox="allow-scripts"
            />
          ) : (
            <div
              className="flex items-center justify-center text-sm text-gray-500 px-4 text-center"
              style={{ height: expanded ? 'calc(100% - 41px)' : '600px' }}
            >
              {pageDoc?.pageId === activePage.page_id && pageDoc.error !== undefined
                ? t('pages.load_failed', { error: pageDoc.error || t('common.unknown_error') })
                : t('pages.loading_page')}
            </div>
          )}
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
