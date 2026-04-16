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
// Auth token (optional)
// -----------------------------------------------------------------------
// Set VITE_API_AUTH_TOKEN in frontend/.env.local (gitignored) to match the
// backend's API_AUTH_TOKEN. When set, every HTTP request and WebSocket
// connection includes the Bearer token.

const AUTH_TOKEN = import.meta.env.VITE_API_AUTH_TOKEN || ''

function _authHeaders() {
  const h = {}
  if (AUTH_TOKEN) h['Authorization'] = `Bearer ${AUTH_TOKEN}`
  return h
}

// -----------------------------------------------------------------------
// Generic fetch wrappers
// -----------------------------------------------------------------------

async function _get(path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: _authHeaders() })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
  }
  return data
}

async function _put(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
  }
  return data
}

async function _post(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
  }
  return data
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
  // custom headers on WebSocket handshakes).
  if (AUTH_TOKEN) {
    const sep = url.includes('?') ? '&' : '?'
    url += `${sep}token=${encodeURIComponent(AUTH_TOKEN)}`
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
    if (!res.ok) return { success: false, error: data.detail || data.error || `HTTP ${res.status}` }
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
        if (!res.ok) return { success: false, error: data.detail || `HTTP ${res.status}` }
        return data
      }),
  setSkillState: (disabled) => _put('/skills/state', { disabled }),

  // ------------------------------------------------------------------ //
  // Personalised Pages
  // ------------------------------------------------------------------ //

  listPersonalisedPages: () => _get('/personalised-pages/list'),

  deletePersonalisedPage: (pageId) =>
    fetch(`${API_BASE}/personalised-pages/${pageId}`, { method: 'DELETE', headers: _authHeaders() })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}))
        if (!res.ok) return { success: false, error: data.detail || `HTTP ${res.status}` }
        return data
      }),
}
