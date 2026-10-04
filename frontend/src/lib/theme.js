/**
 * Theme preference: 'system' | 'light' | 'dark'.
 *
 * The choice lives in localStorage and is applied as `data-theme` on <html>
 * (absent = follow the OS). index.html runs the same logic before React renders
 * so there is no flash of the wrong theme.
 *
 * State is a tiny module-level store so every `useTheme()` caller (the sidebar
 * toggle, charts, syntax highlighter) stays in sync.
 */
import { useSyncExternalStore } from 'react'

export const THEME_KEY = 'hime_theme'
export const THEME_CHOICES = ['system', 'light', 'dark']
const META_COLORS = { light: '#F9F5F0', dark: '#1c1814' }

export function readStoredTheme() {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return THEME_CHOICES.includes(v) ? v : 'system'
  } catch {
    return 'system'
  }
}

export function systemPrefersDark() {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

export function resolveTheme(choice) {
  if (choice === 'light' || choice === 'dark') return choice
  return systemPrefersDark() ? 'dark' : 'light'
}

export function applyTheme(choice) {
  const root = document.documentElement
  if (choice === 'light' || choice === 'dark') root.setAttribute('data-theme', choice)
  else root.removeAttribute('data-theme')
  const resolved = resolveTheme(choice)
  // Keep the theme-color meta in step with the *effective* theme.
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
    m.removeAttribute('media')
    m.setAttribute('content', META_COLORS[resolved])
  })
}

// ---- store ---------------------------------------------------------------

const listeners = new Set()
let state = null
let mq = null

function getState() {
  if (!state) {
    const choice = readStoredTheme()
    state = { choice, resolved: resolveTheme(choice) }
  }
  return state
}

function setState(choice) {
  state = { choice, resolved: resolveTheme(choice) }
  listeners.forEach((l) => l())
}

function onSystemChange() {
  const { choice } = getState()
  if (choice !== 'system') return
  applyTheme('system')
  setState('system')
}

function subscribe(listener) {
  listeners.add(listener)
  if (!mq && typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    mq = window.matchMedia('(prefers-color-scheme: dark)') || null
    mq?.addEventListener?.('change', onSystemChange)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && mq) {
      mq.removeEventListener?.('change', onSystemChange)
      mq = null
    }
  }
}

export function setStoredTheme(choice) {
  try {
    if (choice === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, choice)
  } catch { /* storage unavailable (private mode): theme still applies this session */ }
  applyTheme(choice)
  setState(choice)
}

/** Returns { choice, resolved, setChoice }. `resolved` is always 'light' | 'dark'. */
export function useTheme() {
  const s = useSyncExternalStore(subscribe, getState, getState)
  return { choice: s.choice, resolved: s.resolved, setChoice: setStoredTheme }
}
