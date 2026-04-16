/**
 * Vitest global setup — runs before every test file.
 *
 * Registers @testing-library/jest-dom matchers (toBeInTheDocument, etc.)
 * and stubs browser APIs that jsdom does not provide.
 */

// Initialise i18n before any component import so useTranslation() works.
// The production config uses LanguageDetector which reads navigator /
// localStorage — both available in jsdom, so a direct import is fine.
// We force English so assertions match the en.json translations.
import i18n from '../i18n'
i18n.changeLanguage('en')

import '@testing-library/jest-dom'

// ---------------------------------------------------------------------------
// Stub WebSocket — jsdom does not have one
// ---------------------------------------------------------------------------

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  constructor(url) {
    this.url = url
    this.readyState = MockWebSocket.CONNECTING
    this.onopen = null
    this.onmessage = null
    this.onerror = null
    this.onclose = null

    // Auto-open on next tick so tests can attach handlers first
    Promise.resolve().then(() => {
      this.readyState = MockWebSocket.OPEN
      if (this.onopen) this.onopen(new Event('open'))
    })
  }

  send(data) {
    // no-op in tests
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    if (this.onclose) this.onclose(new CloseEvent('close'))
  }
}

// Only assign if there is no native WebSocket (jsdom typically lacks one)
if (typeof globalThis.WebSocket === 'undefined') {
  globalThis.WebSocket = MockWebSocket
}

// ---------------------------------------------------------------------------
// Stub window.alert / window.confirm — jsdom provides them but they throw
// if not overridden, and several components call alert() on error paths.
// ---------------------------------------------------------------------------

globalThis.alert = vi.fn()
globalThis.confirm = vi.fn(() => true)

// ---------------------------------------------------------------------------
// Stub window.matchMedia — some libraries (e.g. recharts responsive) need it
// ---------------------------------------------------------------------------

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// ---------------------------------------------------------------------------
// Stub ResizeObserver — recharts uses it internally
// ---------------------------------------------------------------------------

globalThis.ResizeObserver = class ResizeObserver {
  constructor(cb) { this._cb = cb }
  observe() {}
  unobserve() {}
  disconnect() {}
}

// ---------------------------------------------------------------------------
// Stub localStorage for AutonomousAgentMonitor's stored config
// ---------------------------------------------------------------------------

const localStorageStore = {}
Object.defineProperty(window, 'localStorage', {
  value: {
    getItem: vi.fn((key) => localStorageStore[key] ?? null),
    setItem: vi.fn((key, val) => { localStorageStore[key] = String(val) }),
    removeItem: vi.fn((key) => { delete localStorageStore[key] }),
    clear: vi.fn(() => { Object.keys(localStorageStore).forEach((k) => delete localStorageStore[k]) }),
  },
})

// ---------------------------------------------------------------------------
// Suppress React 18 act() warnings in async context-heavy tests
// ---------------------------------------------------------------------------

const origError = console.error
console.error = (...args) => {
  if (typeof args[0] === 'string' && args[0].includes('act(')) return
  origError(...args)
}
