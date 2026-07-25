/**
 * Tests for the runtime API-token plumbing.
 *
 * Covers:
 *  - lib/api.js token storage (sessionStorage / localStorage precedence,
 *    build-time fallback, clearing)
 *  - Authorization header + WebSocket ?token= use the token available at
 *    call time, not at module-load time
 *  - 401/403 responses notify onAuthRequired subscribers
 *  - <AuthTokenPrompt> opens on an auth failure, stores what the user typed
 *    and reloads; dismissing keeps it quiet
 */
import { render, screen, act, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'

// ---------------------------------------------------------------------------
// Part 1 — lib/api.js (real module)
// ---------------------------------------------------------------------------

const KEY = 'hime.apiAuthToken'

describe('api runtime auth token', () => {
  let api, getAuthToken, setAuthToken, clearAuthToken, onAuthRequired

  beforeEach(async () => {
    // importActual: the <AuthTokenPrompt> block below mocks '../lib/api', and
    // these tests need the real storage helpers.
    const mod = await vi.importActual('../lib/api')
    ;({ api, getAuthToken, setAuthToken, clearAuthToken, onAuthRequired } = mod)
    window.sessionStorage.clear()
    window.localStorage.clear()
  })

  afterEach(() => {
    window.sessionStorage.clear()
    window.localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('returns an empty token when nothing is stored', () => {
    expect(getAuthToken()).toBe('')
  })

  it('stores the token in sessionStorage by default', () => {
    setAuthToken('abc123')
    expect(window.sessionStorage.getItem(KEY)).toBe('abc123')
    expect(window.localStorage.getItem(KEY)).toBeNull()
    expect(getAuthToken()).toBe('abc123')
  })

  it('stores the token in localStorage when remembering the device', () => {
    setAuthToken('abc123', { remember: true })
    expect(window.localStorage.getItem(KEY)).toBe('abc123')
    expect(window.sessionStorage.getItem(KEY)).toBeNull()
    expect(getAuthToken()).toBe('abc123')
  })

  it('trims the token and clears the other store when switching', () => {
    setAuthToken('  spaced  ', { remember: true })
    expect(window.localStorage.getItem(KEY)).toBe('spaced')
    setAuthToken('session-one')
    expect(window.sessionStorage.getItem(KEY)).toBe('session-one')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('prefers sessionStorage over localStorage', () => {
    window.localStorage.setItem(KEY, 'from-local')
    window.sessionStorage.setItem(KEY, 'from-session')
    expect(getAuthToken()).toBe('from-session')
  })

  it('clearAuthToken removes the token from both stores', () => {
    window.localStorage.setItem(KEY, 'from-local')
    window.sessionStorage.setItem(KEY, 'from-session')
    clearAuthToken()
    expect(getAuthToken()).toBe('')
  })

  it('sends the token stored AFTER module load as a Bearer header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
    })
    vi.stubGlobal('fetch', fetchMock)

    // No token yet → no Authorization header.
    await api.getDataSource()
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined()

    // Token entered at runtime → picked up by the very next request, with no
    // rebuild and no page reload of the api module.
    setAuthToken('runtime-token')
    await api.getDataSource()
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer runtime-token')
  })

  it('appends the runtime token to WebSocket URLs', () => {
    const sockets = []
    class FakeWS {
      constructor(url) { this.url = url; sockets.push(this) }
      close() {}
    }
    vi.stubGlobal('WebSocket', FakeWS)

    api.connectDataStream()
    expect(sockets[0].url).not.toContain('token=')

    setAuthToken('ws token/1')
    api.connectDataStream()
    expect(sockets[1].url).toContain('?token=ws%20token%2F1')

    api.connectAgentMonitor()
    expect(sockets[2].url).toContain('/api/stream/agent/LiveUser?token=ws%20token%2F1')
  })

  it('notifies subscribers on 401 and 403 but not on other errors', async () => {
    const seen = []
    const unsubscribe = onAuthRequired((status) => seen.push(status))

    const respond = (status) => vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status,
      json: async () => ({ detail: 'nope' }),
    }))

    respond(401)
    expect(await api.getDataSource()).toEqual({ success: false, error: 'nope' })
    respond(403)
    await api.setStreamConfig(true)
    respond(500)
    await api.getDataSource()
    respond(401)
    await api.updateScheduledTask('1', {})

    expect(seen).toEqual([401, 403, 401])

    unsubscribe()
    respond(401)
    await api.getDataSource()
    expect(seen).toEqual([401, 403, 401])
  })
})

// ---------------------------------------------------------------------------
// Part 2 — <AuthTokenPrompt> (api module mocked)
// ---------------------------------------------------------------------------

const authMock = vi.hoisted(() => ({
  listeners: new Set(),
  setAuthToken: vi.fn(),
  reloadForAuth: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    setAuthToken: authMock.setAuthToken,
    reloadForAuth: authMock.reloadForAuth,
    onAuthRequired: (fn) => {
      authMock.listeners.add(fn)
      return () => authMock.listeners.delete(fn)
    },
  }
})

import AuthTokenPrompt from '../components/AuthTokenPrompt'

/** Simulate api.js reporting a 401. */
function fireAuthRequired(status = 401) {
  act(() => {
    authMock.listeners.forEach((fn) => fn(status))
  })
}

describe('AuthTokenPrompt', () => {
  beforeEach(() => {
    authMock.listeners.clear()
    authMock.setAuthToken.mockClear()
    authMock.reloadForAuth.mockClear()
  })

  it('stays hidden until an auth failure happens', () => {
    render(<AuthTokenPrompt />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireAuthRequired()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('API token required')).toBeInTheDocument()
  })

  it('stores the pasted token in sessionStorage and reloads', async () => {
    const user = userEvent.setup()
    render(<AuthTokenPrompt />)
    fireAuthRequired()

    await user.type(screen.getByLabelText('API token'), 'pasted-token')
    await user.click(screen.getByRole('button', { name: 'Save and reload' }))

    expect(authMock.setAuthToken).toHaveBeenCalledWith('pasted-token', { remember: false })
    expect(authMock.reloadForAuth).toHaveBeenCalledTimes(1)
  })

  it('honours the "remember on this device" choice', async () => {
    const user = userEvent.setup()
    render(<AuthTokenPrompt />)
    fireAuthRequired()

    await user.type(screen.getByLabelText('API token'), 'pasted-token')
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Save and reload' }))

    expect(authMock.setAuthToken).toHaveBeenCalledWith('pasted-token', { remember: true })
  })

  it('does not submit a blank token', async () => {
    const user = userEvent.setup()
    render(<AuthTokenPrompt />)
    fireAuthRequired()

    const submit = screen.getByRole('button', { name: 'Save and reload' })
    expect(submit).toBeDisabled()
    await user.click(submit)
    expect(authMock.setAuthToken).not.toHaveBeenCalled()
    expect(authMock.reloadForAuth).not.toHaveBeenCalled()
  })

  it('stays closed after being dismissed, even if more 401s arrive', async () => {
    const user = userEvent.setup()
    render(<AuthTokenPrompt />)
    fireAuthRequired()

    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    fireAuthRequired()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
