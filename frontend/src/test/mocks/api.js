/**
 * Mock for ../lib/api.js — every public method stubbed with vi.fn()
 * and sensible default return values matching Apple Health / Live HealthKit format.
 */
import { vi } from 'vitest'

// ---------------------------------------------------------------------------
// Realistic mock data
// ---------------------------------------------------------------------------

export const mockFeatureTypes = [
  'Heart Rate',
  'Steps',
  'Active Energy',
  'Blood Oxygen',
  'Heart Rate Variability',
  'Resting Heart Rate',
  'Sleep Analysis',
  'Respiratory Rate',
  'Walking Step Length',
  'Exercise Time',
]

export const mockReports = [
  {
    id: 1,
    title: 'Elevated Resting Heart Rate Detected',
    content: '## Summary\nYour resting heart rate averaged **72 BPM** over the past 24 hours, which is above your baseline of 65 BPM.\n\n### Recommendation\n- Consider checking stress levels\n- Monitor for the next 48 hours',
    alert_level: 'warning',
    created_at: '2026-03-20T10:30:00Z',
    time_range_end: '2026-03-20T10:00:00Z',
    metadata: {
      data_timestamp: '2026-03-20T10:00:00Z',
      tags: ['heart_rate', 'anomaly'],
    },
  },
  {
    id: 2,
    title: 'Daily Activity Goal Achieved',
    content: '## Great Job!\nYou hit **12,500 steps** today, exceeding your 10,000-step goal by 25%.',
    alert_level: 'normal',
    created_at: '2026-03-19T22:00:00Z',
    time_range_end: '2026-03-19T21:00:00Z',
    metadata: {
      data_timestamp: '2026-03-19T21:00:00Z',
      tags: ['steps', 'goal'],
    },
  },
  {
    id: 3,
    title: 'Critical: Irregular Heart Rhythm Detected',
    content: '## Alert\nMultiple irregular heart rhythm events detected between 02:00-04:00.',
    alert_level: 'critical',
    created_at: '2026-03-18T05:00:00Z',
    time_range_end: '2026-03-18T04:00:00Z',
    metadata: {
      data_timestamp: '2026-03-18T04:00:00Z',
      tags: ['heart_rhythm', 'critical'],
    },
  },
]

export const mockMemoryStats = {
  table_counts: {
    reports: 12,
    activity_log: 340,
    user_notes: 5,
    health_insights: 28,
  },
  date_range: {
    min: '2026-01-01T00:00:00Z',
    max: '2026-03-20T10:00:00Z',
  },
}

export const mockTools = [
  {
    type: 'function',
    function: {
      name: 'sql',
      description: 'Execute SQL queries on health_data (read-only) or memory (read-write) database.',
      parameters: {
        type: 'object',
        properties: {
          database: { type: 'string', enum: ['health_data', 'memory'], description: 'Target database.' },
          query: { type: 'string', description: 'SQL query to execute.' },
        },
        required: ['database', 'query'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'code',
      description: 'Execute Python code with pandas/numpy in a sandboxed environment.',
      parameters: {
        type: 'object',
        properties: {
          code: { type: 'string', description: 'Python code to execute' },
        },
        required: ['code'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'push_report',
      description: 'Push an analysis report to the dashboard and Telegram.',
      parameters: {
        type: 'object',
        properties: {
          title: { type: 'string', description: 'Report title' },
          content: { type: 'string', description: 'Markdown report content' },
          alert_level: { type: 'string', description: 'normal | info | warning | critical' },
        },
        required: ['title', 'content'],
      },
    },
  },
]

export const mockStreamConfig = {
  success: true,
  is_streaming: false,
  granularity: 'real-time',
  live_history_window: '1hour',
}

export const mockAgentStatus = {
  success: true,
  agents: {
    LiveUser: {
      status: 'running',
      user_id: 'LiveUser',
      uptime_seconds: 3600,
      cycles_completed: 12,
      reports_generated: 5,
    },
  },
}

export const mockAgentStatusEmpty = {
  success: true,
  agents: {},
}

export const mockFeatureMetadata = {
  success: true,
  features: {
    'Heart Rate': { unit: 'BPM', category: 'Vitals', description: 'Heart beats per minute' },
    'Steps': { unit: 'Count', category: 'Activity', description: 'Step count' },
    'Active Energy': { unit: 'kcal', category: 'Activity', description: 'Calories burned from activity' },
  },
}

export const mockTableInspectData = {
  success: true,
  rows: [
    { id: 1, title: 'Test Report', created_at: '2026-03-20T10:00:00Z' },
    { id: 2, title: 'Another Report', created_at: '2026-03-19T10:00:00Z' },
  ],
}

// ---------------------------------------------------------------------------
// Mocked api object — mirrors the shape of lib/api.js
// ---------------------------------------------------------------------------

export const api = {
  // Data source
  getDataSource: vi.fn().mockResolvedValue({ success: true, data_source: 'live_healthkit' }),
  reloadDataReader: vi.fn().mockResolvedValue({ success: true }),

  // Participants & features
  getParticipants: vi.fn().mockResolvedValue({ success: true, users: ['LiveUser'] }),
  getFeatureTypes: vi.fn().mockResolvedValue({ success: true, feature_types: mockFeatureTypes }),
  getParticipantFeatures: vi.fn().mockResolvedValue({ success: true, data: [] }),
  getFeatureMetadata: vi.fn().mockResolvedValue(mockFeatureMetadata),
  inspectParticipantData: vi.fn().mockResolvedValue({ success: true, data: [] }),

  // Stream configuration
  getStreamConfig: vi.fn().mockResolvedValue(mockStreamConfig),
  getDefaults: vi.fn().mockResolvedValue({ success: true, llm_provider: 'gemini', model: 'gemini-2.5-flash', data_source: 'live_healthkit' }),
  setStreamConfig: vi.fn().mockResolvedValue({ success: true }),
  setParticipants: vi.fn().mockResolvedValue({ success: true }),

  // Agent V2
  startAutonomousAgent: vi.fn().mockResolvedValue({ success: true }),
  stopAutonomousAgent: vi.fn().mockResolvedValue({ success: true }),
  getAgentLastConfig: vi.fn().mockResolvedValue({ success: true, config: { llm_provider: 'gemini', model: 'gemini-2.5-flash' } }),
  getAgentStatus: vi.fn().mockResolvedValue(mockAgentStatusEmpty),
  getAgentActivity: vi.fn().mockResolvedValue({ success: true, events: [] }),
  queryAgentMemory: vi.fn().mockResolvedValue({ success: true, data: mockMemoryStats }),
  getTools: vi.fn().mockResolvedValue({ success: true, tools: mockTools }),
  inspectMemoryTable: vi.fn().mockResolvedValue(mockTableInspectData),
  getScheduledTasks: vi.fn().mockResolvedValue({ success: true, tasks: [] }),
  createScheduledTask: vi.fn().mockResolvedValue({ success: true }),
  updateScheduledTask: vi.fn().mockResolvedValue({ success: true }),
  triggerAnalysis: vi.fn().mockResolvedValue({ success: true }),

  // WebSocket connections — return a mock WebSocket-like object
  connectDataStream: vi.fn(() => {
    const ws = new WebSocket('ws://localhost/api/stream/data')
    return ws
  }),
  connectAgentMonitor: vi.fn(() => {
    const ws = new WebSocket('ws://localhost/api/stream/agent/LiveUser')
    return ws
  }),

  // Prompts
  listPrompts: vi.fn().mockResolvedValue({ success: true, prompts: ['soul', 'job', 'experience', 'user'] }),
  fetchPrompt: vi.fn().mockResolvedValue({ success: true, content: '# Test Prompt' }),
  savePrompt: vi.fn().mockResolvedValue({ success: true }),

  // Personalised Pages
  listPersonalisedPages: vi.fn().mockResolvedValue({ success: true, apps: [] }),
  deletePersonalisedPage: vi.fn().mockResolvedValue({ success: true }),
}
