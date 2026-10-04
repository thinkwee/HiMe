/**
 * lib/api.js — URL encoding, header merging, error contract.
 */
import { api, setAuthToken, clearAuthToken } from '../lib/api'

function okResponse(body = { success: true }) {
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body), blob: async () => new Blob(['png']) }
}

describe('api', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(okResponse())
    vi.stubGlobal('fetch', fetchMock)
    clearAuthToken()
  })

  afterEach(() => {
    clearAuthToken()
    vi.unstubAllGlobals()
  })

  const urlOf = (i = 0) => fetchMock.mock.calls[i][0]

  it('percent-encodes path segments and query values', async () => {
    await api.updateScheduledTask('1/../2', { status: 'paused' })
    expect(urlOf(0)).toBe('/api/agent/scheduled-tasks/LiveUser/1%2F..%2F2')
    await api.updateTriggerRule('a b', {})
    expect(urlOf(1)).toBe('/api/agent/trigger-rules/LiveUser/a%20b')
    await api.inspectMemoryTable('t&x=1', 5)
    expect(urlOf(2)).toBe('/api/agent/memory/LiveUser/inspect?table_name=t%26x%3D1&limit=5')
    await api.fetchPrompt('soul/../x')
    expect(urlOf(3)).toBe('/api/prompts/soul%2F..%2Fx')
    await api.savePrompt('a#b', 'c')
    expect(urlOf(4)).toBe('/api/prompts/a%23b')
    await api.fetchSkill('my skill')
    expect(urlOf(5)).toBe('/api/skills/my%20skill')
    await api.deleteSkill('x/y')
    expect(urlOf(6)).toBe('/api/skills/x%2Fy')
    await api.deletePersonalisedPage('p/1')
    expect(urlOf(7)).toBe('/api/personalised-pages/p%2F1')
  })

  it('caller-supplied headers can never drop the Authorization header', async () => {
    setAuthToken('tok')
    // getDataCount forwards options straight to _get; simulate a caller header via a signal-only call
    await api.getDataCount()
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer tok')
    await api.deleteSkill('s')
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer tok')
  })

  it('inspectParticipantData follows the {success:false} contract instead of throwing', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(api.inspectParticipantData('LiveUser')).resolves.toEqual({ success: false, error: 'offline' })

    fetchMock.mockResolvedValueOnce({ ok: false, status: 502, text: async () => '<html>Bad gateway</html>' })
    await expect(api.inspectParticipantData('LiveUser')).resolves.toEqual({ success: false, error: 'HTTP 502' })

    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, text: async () => '{"success": true, "v": NaN}' })
    await expect(api.inspectParticipantData('LiveUser')).resolves.toEqual({ success: true, v: null })
  })

  it('delete helpers return {success:false} on network errors', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(api.deleteSkill('s')).resolves.toEqual({ success: false, error: 'offline' })
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(api.deletePersonalisedPage('p')).resolves.toEqual({ success: false, error: 'offline' })
  })

  it('fetchChatImage sends the bearer header (never ?token=) and returns a blob', async () => {
    setAuthToken('img-token')
    const res = await api.fetchChatImage('/api/agent/chat-image/abc')
    expect(res.success).toBe(true)
    expect(res.blob).toBeInstanceOf(Blob)
    expect(urlOf(0)).toBe('/api/agent/chat-image/abc')
    expect(urlOf(0)).not.toContain('token=')
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer img-token')

    await api.fetchChatImage('raw id')
    expect(urlOf(1)).toBe('/api/agent/chat-image/raw%20id')
  })

  it('fetchChatImage reports failures', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404 })
    await expect(api.fetchChatImage('/api/agent/chat-image/x')).resolves.toEqual({ success: false, error: 'HTTP 404' })
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(api.fetchChatImage('x')).resolves.toEqual({ success: false, error: 'offline' })
  })
})
