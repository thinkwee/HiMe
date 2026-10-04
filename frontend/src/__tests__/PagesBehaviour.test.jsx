/**
 * Cross-page behaviour: refetch on route activation, Skills / PromptEditor editor
 * safety (dirty check, stale responses, double-save), error banners.
 */
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../lib/api', () => import('../test/mocks/api'))

import Skills from '../pages/Skills'
import PromptEditor from '../pages/PromptEditor'
import KnowledgeBase from '../pages/KnowledgeBase'
import ReportsView from '../pages/ReportsView'
import PersonalisedPages from '../pages/PersonalisedPages'
import { api } from '../test/mocks/api'

const SKILLS = [
  { name: 'alpha', description: 'first', enabled: true },
  { name: 'beta', description: 'second', enabled: true },
]

function mockSkills() {
  api.listSkills.mockResolvedValue({ success: true, skills: SKILLS })
  api.fetchSkill.mockImplementation((name) =>
    Promise.resolve({ success: true, name, description: `${name} desc`, body: `${name} body` }))
}

describe('Skills', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSkills()
    api.updateSkill.mockResolvedValue({ success: true })
    api.setSkillState.mockResolvedValue({ success: true })
  })

  const body = () => screen.getByLabelText('Playbook body')

  it('asks before discarding unsaved edits when switching skills', async () => {
    const user = userEvent.setup()
    render(<Skills />)
    await user.click(await screen.findByText('alpha'))
    await waitFor(() => expect(body()).toHaveValue('alpha body'))

    await user.type(body(), ' EDITED')
    // declining keeps the edits and does not load the other skill
    window.confirm.mockReturnValueOnce(false)
    await user.click(screen.getByText('beta'))
    expect(window.confirm).toHaveBeenCalled()
    expect(body()).toHaveValue('alpha body EDITED')
    expect(api.fetchSkill).toHaveBeenCalledTimes(1)

    // accepting switches
    window.confirm.mockReturnValueOnce(true)
    await user.click(screen.getByText('beta'))
    await waitFor(() => expect(body()).toHaveValue('beta body'))
  })

  it('does not prompt when nothing was edited', async () => {
    const user = userEvent.setup()
    render(<Skills />)
    await user.click(await screen.findByText('alpha'))
    await waitFor(() => expect(body()).toHaveValue('alpha body'))
    await user.click(screen.getByText('beta'))
    await waitFor(() => expect(body()).toHaveValue('beta body'))
    expect(window.confirm).not.toHaveBeenCalled()
  })

  it('prompts before "New" discards edits', async () => {
    const user = userEvent.setup()
    render(<Skills />)
    await user.click(await screen.findByText('alpha'))
    await waitFor(() => expect(body()).toHaveValue('alpha body'))
    await user.type(body(), '!')
    window.confirm.mockReturnValueOnce(false)
    await user.click(screen.getByRole('button', { name: /New/ }))
    expect(body()).toHaveValue('alpha body!')
  })

  it('ignores an out-of-order (stale) skill response', async () => {
    const user = userEvent.setup()
    let resolveAlpha
    api.fetchSkill.mockImplementation((name) =>
      name === 'alpha'
        ? new Promise((r) => { resolveAlpha = () => r({ success: true, name: 'alpha', description: 'a', body: 'alpha body' }) })
        : Promise.resolve({ success: true, name: 'beta', description: 'b', body: 'beta body' }))
    render(<Skills />)
    await user.click(await screen.findByText('alpha'))
    await user.click(screen.getByText('beta'))
    await waitFor(() => expect(body()).toHaveValue('beta body'))
    await act(async () => { resolveAlpha() })
    expect(body()).toHaveValue('beta body')
  })

  it('toggling visibility does not reload (and overwrite) the editor', async () => {
    const user = userEvent.setup()
    render(<Skills />)
    await user.click(await screen.findByText('alpha'))
    await waitFor(() => expect(body()).toHaveValue('alpha body'))
    await user.type(body(), ' keep me')
    api.fetchSkill.mockClear()
    // Failing PUT forces a list reload ("reconcile") — it must leave the editor alone.
    api.setSkillState.mockResolvedValueOnce({ success: false, error: 'nope' })
    const toggles = screen.getAllByRole('button', { name: 'Hide from agent' })
    await user.click(toggles[1])
    await waitFor(() => expect(api.listSkills.mock.calls.length).toBeGreaterThan(1))
    expect(api.fetchSkill).not.toHaveBeenCalled()
    expect(body()).toHaveValue('alpha body keep me')
  })

  it('Ctrl+S while a save is in flight does not send a second request', async () => {
    const user = userEvent.setup()
    let release
    api.updateSkill.mockImplementation(() => new Promise((r) => { release = r }))
    render(<Skills />)
    await user.click(await screen.findByText('alpha'))
    await waitFor(() => expect(body()).toHaveValue('alpha body'))
    fireEvent.keyDown(body(), { key: 's', ctrlKey: true })
    fireEvent.keyDown(body(), { key: 's', ctrlKey: true })
    expect(api.updateSkill).toHaveBeenCalledTimes(1)
    await act(async () => { release({ success: true }) })
  })
})

describe('PromptEditor', () => {
  it('Ctrl+S while a save is in flight does not send a second request', async () => {
    vi.clearAllMocks()
    api.listPrompts.mockResolvedValue({
      success: true,
      prompts: [{ id: 'soul', title: 'Soul', file: 'soul.md', content: 'hello', agent_editable: false }],
    })
    let release
    api.savePrompt.mockImplementation(() => new Promise((r) => { release = r }))
    render(<PromptEditor />)
    const area = await screen.findByLabelText('Soul')
    fireEvent.keyDown(area, { key: 's', ctrlKey: true })
    fireEvent.keyDown(area, { key: 's', ctrlKey: true })
    expect(api.savePrompt).toHaveBeenCalledTimes(1)
    await act(async () => { release({ success: true }) })
  })
})

describe('refetch when the route becomes active', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getTools.mockResolvedValue({ success: true, tools: [] })
    api.queryAgentMemory.mockResolvedValue({ success: true, data: { table_counts: { reports: 2 }, date_range: {} } })
    api.listPersonalisedPages.mockResolvedValue({ success: true, pages: [] })
    api.inspectMemoryTable.mockResolvedValue({ success: true, rows: [] })
  })

  it('KnowledgeBase refetches on activation and has a refresh button', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<KnowledgeBase active={false} />)
    await waitFor(() => expect(api.getTools).toHaveBeenCalledTimes(1))
    rerender(<KnowledgeBase active />)
    await waitFor(() => expect(api.getTools).toHaveBeenCalledTimes(2))

    await screen.findByText('reports')
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(api.getTools).toHaveBeenCalledTimes(3))
  })

  it('KnowledgeBase shows an error with retry when loading fails', async () => {
    api.getTools.mockResolvedValue({ success: false, error: 'tools down' })
    render(<KnowledgeBase active />)
    expect(await screen.findByRole('alert')).toHaveTextContent('tools down')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('ReportsView refetches silently (keeps the list) on activation', async () => {
    api.queryAgentMemory.mockResolvedValue({
      success: true,
      data: [{ id: 7, title: 'Seven', content: 'c', alert_level: 'normal', created_at: '2026-03-20T10:00:00Z', metadata: {} }],
    })
    const { rerender } = render(<ReportsView active={false} />)
    expect(await screen.findByText('Seven')).toBeInTheDocument()
    const calls = api.queryAgentMemory.mock.calls.length
    rerender(<ReportsView active />)
    await waitFor(() => expect(api.queryAgentMemory.mock.calls.length).toBe(calls + 1))
    expect(screen.getByText('Seven')).toBeInTheDocument()
  })

  it('ReportsView report cards are keyboard accessible and the modal is a dialog closed by Escape', async () => {
    const user = userEvent.setup()
    api.queryAgentMemory.mockResolvedValue({
      success: true,
      data: [{ id: 7, title: 'Seven', content: 'body text', alert_level: 'normal', created_at: '2026-03-20T10:00:00Z', metadata: {} }],
    })
    render(<ReportsView active />)
    const card = (await screen.findByText('Seven')).closest('[role="button"]')
    card.focus()
    await user.keyboard('{Enter}')
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('PersonalisedPages refetches on activation and shows a retry banner on failure', async () => {
    const { rerender } = render(<PersonalisedPages active={false} />)
    await waitFor(() => expect(api.listPersonalisedPages).toHaveBeenCalledTimes(1))
    api.listPersonalisedPages.mockResolvedValue({ success: false, error: 'pages down' })
    rerender(<PersonalisedPages active />)
    expect(await screen.findByRole('alert')).toHaveTextContent('pages down')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('PersonalisedPages delete failures are shown inline, not via alert()', async () => {
    const user = userEvent.setup()
    api.listPersonalisedPages.mockResolvedValue({
      success: true,
      pages: [{ page_id: 'p1', display_name: 'Page One', description: '' }],
    })
    api.deletePersonalisedPage.mockResolvedValue({ success: false, error: 'locked' })
    render(<PersonalisedPages active />)
    await screen.findByText('Page One')
    await user.click(screen.getByRole('button', { name: 'Delete page' }))
    expect(await screen.findByText(/locked/)).toBeInTheDocument()
    expect(globalThis.alert).not.toHaveBeenCalled()
  })
})
