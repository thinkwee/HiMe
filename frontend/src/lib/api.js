/**
 * API client — all backend HTTP + WebSocket calls.
 *
 * Convention
 * ----------
 * Every method returns a plain object.  On HTTP success the object has
 * `{success: true, ...}`.  On HTTP error it has `{success: false, error: string}`.
 * Callers should never need to check `response.ok` themselves.
 */

const API_BASE = '/api'

// -----------------------------------------------------------------------
// Auth token (optional) — resolved at RUNTIME, never baked into the bundle
// -----------------------------------------------------------------------
// The backend enables bearer auth whenever API_AUTH_TOKEN is set. The token
// is looked up on every single request, in this order:
//
//   1. sessionStorage  — what <AuthTokenPrompt> writes by default. Lives only
//                        as long as the tab, so a shared/other-user browser
//                        does not keep the secret around.
//   2. localStorage    — only when the user ticked "remember on this device".
//   3. VITE_API_AUTH_TOKEN — build-time fallback, kept so existing native /
//                        dev setups (hime.sh writes frontend/.env.local) keep
//                        working unchanged.
//
// Docker images are built once and shipped to everyone, so a build-time-only
// token is both impossible to configure (no build arg) and wrong to use: the
// bundle in dist/ is served to every browser that loads the dashboard.

const TOKEN_STORAGE_KEY = 'hime.apiAuthToken'
const BUILD_TIME_TOKEN = import.meta.env.VITE_API_AUTH_TOKEN || ''

/** Storage accessor that survives Safari private mode / disabled storage. */
function _store(kind) {
  try {
    const s = kind === 'local' ? window.localStorage : window.sessionStorage
    // Touch the API so a throwing/absent implementation is caught here.
    return s && typeof s.getItem === 'function' ? s : null
  } catch (_) {
    return null
  }
}

function _read(kind) {
  try {
    return _store(kind)?.getItem(TOKEN_STORAGE_KEY) || ''
  } catch (_) {
    return ''
  }
}

/** Current bearer token, or '' when auth is not configured. */
export function getAuthToken() {
  return _read('session') || _read('local') || BUILD_TIME_TOKEN
}

/**
 * Persist a token for subsequent requests.
 *
 * @param {string} token           Raw token; blank clears the stored token.
 * @param {object} [opts]
 * @param {boolean} [opts.remember] true → localStorage (survives browser
 *                                  restarts); false → sessionStorage (tab only).
 */
export function setAuthToken(token, { remember = false } = {}) {
  const value = (token || '').trim()
  // Always clear both stores first, otherwise a stale copy in the other one
  // could win (or linger) after the user changes their mind.
  for (const kind of ['session', 'local']) {
    try {
      _store(kind)?.removeItem(TOKEN_STORAGE_KEY)
    } catch (_) { /* storage unavailable — nothing to clear */ }
  }
  if (!value) return
  try {
    _store(remember ? 'local' : 'session')?.setItem(TOKEN_STORAGE_KEY, value)
  } catch (_) { /* quota / private mode — request will simply stay unauthed */ }
}

/** Forget any stored token (both stores). */
export function clearAuthToken() {
  setAuthToken('')
}

/**
 * Reload the page after the token changed.
 *
 * Deliberately blunt: a reload re-runs every page's initial fetch and
 * re-opens every WebSocket with the new token, so no per-page retry plumbing
 * is needed. Kept in this module so it can be stubbed in tests.
 */
export function reloadForAuth() {
  window.location.reload()
}

// --- 401/403 notification -------------------------------------------------
// api.js has no UI, so it just broadcasts; <AuthTokenPrompt> subscribes and
// asks the user for a token.

const _authListeners = new Set()

/** Subscribe to auth failures. Returns an unsubscribe function. */
export function onAuthRequired(listener) {
  _authListeners.add(listener)
  return () => _authListeners.delete(listener)
}

function _noteAuthStatus(status) {
  if (status !== 401 && status !== 403) return
  for (const listener of Array.from(_authListeners)) {
    try {
      listener(status)
    } catch (e) {
      console.error('auth listener failed:', e)
    }
  }
}

function _authHeaders() {
  const h = {}
  const token = getAuthToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

// -----------------------------------------------------------------------
// Generic fetch wrappers
// -----------------------------------------------------------------------

async function _get(path, options = {}) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: _authHeaders(), ...options })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      _noteAuthStatus(res.status)
      return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
    }
    return data
  } catch (e) {
    // Network-level failure (backend down, request aborted). Honour the
    // contract above: callers only ever have to inspect `success`.
    return { success: false, error: e?.message || 'Network error' }
  }
}

async function _put(path, body) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ..._authHeaders() },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      _noteAuthStatus(res.status)
      return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
    }
    return data
  } catch (e) {
    return { success: false, error: e?.message || 'Network error' }
  }
}

async function _post(path, body) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ..._authHeaders() },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      _noteAuthStatus(res.status)
      return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
    }
    return data
  } catch (e) {
    return { success: false, error: e?.message || 'Network error' }
  }
}

// -----------------------------------------------------------------------
// WebSocket helper
// -----------------------------------------------------------------------

function _ws(path) {
  let url;
  if (API_BASE.startsWith('http')) {
    const wsBase = API_BASE.replace('http', 'ws');
    url = `${wsBase}${path}`;
  } else {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    url = `${proto}//${window.location.host}${API_BASE}${path}`;
  }
  // Append auth token as query parameter for WebSocket (browsers can't set
  // custom headers on WebSocket handshakes). Read at connect time, so a token
  // entered in <AuthTokenPrompt> is picked up by every later connection —
  // including the auto-reconnects of the data stream and agent monitor.
  const token = getAuthToken()
  if (token) {
    const sep = url.includes('?') ? '&' : '?'
    url += `${sep}token=${encodeURIComponent(token)}`
  }
  return new WebSocket(url);
}

// -----------------------------------------------------------------------
// Public API surface
// -----------------------------------------------------------------------

export const api = {
  // ------------------------------------------------------------------ //
  // Data source
  // ------------------------------------------------------------------ //

  /** Returns { success, data_source, available_sources } */
  getDataSource: () => _get('/data/source'),

  /** Force-reload data reader (e.g. after updating files on disk) */
  reloadDataReader: () => _post('/data/reload', {}),

  /** Total stored record count — { success, count }. Accepts an AbortSignal. */
  getDataCount: (signal = null) => _get('/data/count', signal ? { signal } : {}),

  // ------------------------------------------------------------------ //
  // Participants & features
  // ------------------------------------------------------------------ //

  getParticipants: (datasets = null) => {
    const qs = datasets?.length ? '?' + datasets.map(d => `datasets=${d}`).join('&') : ''
    return _get(`/data/users${qs}`)
  },

  getFeatureTypes: () => _get('/data/feature_types'),

  getParticipantFeatures: (pid, featureType = 'steps') =>
    _get(`/data/features/${pid}?feature_type=${encodeURIComponent(featureType)}`),

  getFeatureMetadata: () => _get('/data/feature_metadata'),

  inspectParticipantData: async (pid, featureType = 'steps', limit = 100) => {
    const res = await fetch(
      `${API_BASE}/data/inspect/${pid}?feature_type=${encodeURIComponent(featureType)}&limit=${limit}`,
      { headers: _authHeaders() }
    )
    // Sanitise NaN/Infinity that SQLite may emit before JSON.parse
    const text = (await res.text()).replace(/\bNaN\b|-?Infinity\b/g, 'null')
    const data = JSON.parse(text)
    if (!res.ok) {
      _noteAuthStatus(res.status)
      return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
    }
    return data
  },

  // ------------------------------------------------------------------ //
  // Stream configuration
  // ------------------------------------------------------------------ //

  getStreamConfig: () => _get('/config/stream'),

  /** Returns { llm_provider, model, data_source } from backend .env defaults */
  getDefaults: () => _get('/config/defaults'),

  setStreamConfig: (isStreaming = null, liveHistoryWindow = null) => {
    const body = { granularity: 'real-time' }
    if (isStreaming !== null) body.is_streaming = isStreaming
    if (liveHistoryWindow !== null) body.live_history_window = liveHistoryWindow
    return _post('/config/stream', body)
  },

  setParticipants: (userIds, datasets = null) =>
    _post('/config/users', { user_ids: userIds, datasets }),

  // ------------------------------------------------------------------ //
  // Autonomous Agent V2
  // ------------------------------------------------------------------ //

  startAutonomousAgent: (llmProvider = 'gemini', options = {}) =>
    _post('/agent/start', {
      llm_provider: llmProvider,
      model: options.model || null,
    }),

  stopAutonomousAgent: () =>
    _post('/agent/stop', {}),

  /** Returns full status for all agents or a specific one */
  getAgentLastConfig: () =>
    _get('/agent/last-config'),

  getAgentStatus: (userId = null) =>
    _get(userId
      ? `/agent/status?user_id=${encodeURIComponent(userId)}`
      : '/agent/status'),

  getAgentActivity: (limit = 500) =>
    _get(`/agent/activity/LiveUser?limit=${limit}`),

  queryAgentMemory: (queryType = 'stats') =>
    _get(`/agent/memory/LiveUser?query_type=${queryType}`),

  getTools: () => _get('/agent/tools'),

  inspectMemoryTable: (tableName, limit = 50) =>
    _get(`/agent/memory/LiveUser/inspect?table_name=${tableName}&limit=${limit}`),

  // Scheduled tasks
  getScheduledTasks: () =>
    _get('/agent/scheduled-tasks/LiveUser'),

  createScheduledTask: (cronExpr, promptGoal) =>
    _post('/agent/scheduled-tasks/LiveUser', { cron_expr: cronExpr, prompt_goal: promptGoal }),

  updateScheduledTask: (taskId, updates) =>
    _put(`/agent/scheduled-tasks/LiveUser/${taskId}`, updates),

  triggerAnalysis: (goal = null) =>
    _post('/agent/trigger-analysis/LiveUser', { goal }),

  // Trigger rules
  getTriggerRules: () =>
    _get('/agent/trigger-rules/LiveUser'),

  createTriggerRule: (rule) =>
    _post('/agent/trigger-rules/LiveUser', rule),

  updateTriggerRule: (ruleId, updates) =>
    _put(`/agent/trigger-rules/LiveUser/${ruleId}`, updates),

  // ------------------------------------------------------------------ //
  // WebSocket connections
  // ------------------------------------------------------------------ //

  /** Live data stream → receives batches of streaming health records. */
  connectDataStream: () => _ws('/stream/data'),

  /** Agent monitor stream → receives real-time agent events. */
  connectAgentMonitor: () =>
    _ws('/stream/agent/LiveUser'),

  // ------------------------------------------------------------------ //
  // Prompt Management
  // ------------------------------------------------------------------ //

  listPrompts: () => _get('/prompts'),
  fetchPrompt: (id) => _get(`/prompts/${id}`),
  savePrompt: (id, content) => _post(`/prompts/${id}`, { content }),

  // ------------------------------------------------------------------ //
  // Personalised Pages
  // ------------------------------------------------------------------ //

  // ------------------------------------------------------------------ //
  // Skills (user-written analysis playbooks)
  // ------------------------------------------------------------------ //

  listSkills: () => _get('/skills'),
  fetchSkill: (name) => _get(`/skills/${name}`),
  createSkill: (name, description, body) =>
    _post('/skills', { name, description, body }),
  updateSkill: (name, description, body) =>
    _put(`/skills/${name}`, { description, body }),
  deleteSkill: (name) =>
    fetch(`${API_BASE}/skills/${name}`, { method: 'DELETE', headers: _authHeaders() })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}))
        if (!res.ok) {
          _noteAuthStatus(res.status)
          return { success: false, error: data.detail || `HTTP ${res.status}` }
        }
        return data
      }),
  setSkillState: (disabled) => _put('/skills/state', { disabled }),

  // ------------------------------------------------------------------ //
  // Personalised Pages
  // ------------------------------------------------------------------ //

  listPersonalisedPages: () => _get('/personalised-pages/list'),

  /**
   * Fetch a personalised page's HTML document.
   *
   * The dashboard renders these pages via `srcdoc` rather than pointing an
   * `<iframe src>` at this URL. The endpoint is behind the API bearer token,
   * and the only way to authenticate a frame navigation is `?token=` in the
   * URL — which the sandboxed, agent-generated page could then read straight
   * out of its own `location.search`. Fetching the markup here keeps the token
   * in a request header the page never sees.
   *
   * Returns { success: true, html } or { success: false, status?, error }.
   */
  getPersonalisedPageHtml: (pageId) =>
    fetch(`${API_BASE}/personalised-pages/${encodeURIComponent(pageId)}/`, {
      headers: _authHeaders(),
    })
      .then(async (res) => {
        if (!res.ok) {
          _noteAuthStatus(res.status)
          const data = await res.json().catch(() => ({}))
          return {
            success: false,
            status: res.status,
            error: data.detail || data.error || `HTTP ${res.status}`,
          }
        }
        return { success: true, html: await res.text() }
      })
      .catch((e) => ({ success: false, error: e?.message || 'Network error' })),

  deletePersonalisedPage: (pageId) =>
    fetch(`${API_BASE}/personalised-pages/${pageId}`, { method: 'DELETE', headers: _authHeaders() })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}))
        if (!res.ok) {
          _noteAuthStatus(res.status)
          return { success: false, error: data.detail || `HTTP ${res.status}` }
        }
        return data
      }),
}
