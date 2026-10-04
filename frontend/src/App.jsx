import { memo, useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, FileText, Database, HardDrive, MessageSquare, AppWindow, Sparkles, X, Menu
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { AppProvider, useApp } from './context/AppContext'
import AuthTokenPrompt from './components/AuthTokenPrompt'
import ErrorBoundary from './components/ErrorBoundary'
import LanguageSwitcher from './components/LanguageSwitcher'
import ThemeToggle from './components/ThemeToggle'
import Dashboard from './pages/Dashboard'
import AutonomousAgentMonitor from './pages/AutonomousAgentMonitor'
import ReportsView from './pages/ReportsView'
import PromptEditor from './pages/PromptEditor'
import KnowledgeBase from './pages/KnowledgeBase'
import PersonalisedPages from './pages/PersonalisedPages'
import Skills from './pages/Skills'

// -----------------------------------------------------------------------
// Navigation config
// -----------------------------------------------------------------------

const NAV_ITEMS = [
  { to: '/', icon: Activity, labelKey: 'nav.dashboard' },
  { to: '/agent', icon: Bot, labelKey: 'nav.agent_monitor' },
  { to: '/reports', icon: FileText, labelKey: 'nav.reports' },
  { to: '/prompts', icon: MessageSquare, labelKey: 'nav.prompts' },
  { to: '/skills', icon: Sparkles, labelKey: 'nav.skills' },
  { to: '/knowledge', icon: Database, labelKey: 'nav.knowledge' },
  { to: '/pages', icon: AppWindow, labelKey: 'nav.personalised_pages' },
]

// -----------------------------------------------------------------------
// Persistent views — all pages stay mounted, only visibility toggles.
// Prevents WebSocket/stream/state loss when switching pages.
// -----------------------------------------------------------------------

// Pages are memoised so a re-render of the shell (it reads live stream state
// for the sidebar indicator) doesn't cascade into every mounted page. Each
// page gets an `active` prop so it can refetch on activation and pause its
// timers while hidden.
const DashboardPage = memo(Dashboard)
const AgentPage = memo(AutonomousAgentMonitor)
const ReportsPage = memo(ReportsView)
const PromptsPage = memo(PromptEditor)
const SkillsPage = memo(Skills)
const KnowledgePage = memo(KnowledgeBase)
const PagesPage = memo(PersonalisedPages)

function PersistentViews() {
  const location = useLocation()
  const path = location.pathname

  return (
    <>
      <div className={path === '/' ? 'block' : 'hidden'}>
        <DashboardPage active={path === '/'} />
      </div>
      <div className={path === '/agent' ? 'block' : 'hidden'}>
        <AgentPage active={path === '/agent'} />
      </div>
      <div className={path === '/reports' ? 'block' : 'hidden'}>
        <ReportsPage active={path === '/reports'} />
      </div>
      <div className={path === '/prompts' ? 'block' : 'hidden'}>
        <PromptsPage active={path === '/prompts'} />
      </div>
      <div className={path === '/skills' ? 'block' : 'hidden'}>
        <SkillsPage active={path === '/skills'} />
      </div>
      <div className={path === '/knowledge' ? 'block' : 'hidden'}>
        <KnowledgePage active={path === '/knowledge'} />
      </div>
      <div className={path === '/pages' ? 'block' : 'hidden'}>
        <PagesPage active={path === '/pages'} />
      </div>
    </>
  )
}

// -----------------------------------------------------------------------
// Banners — surface failures that used to be silent
// -----------------------------------------------------------------------

function ErrorBanners() {
  const { globalError, streaming, dispatch, reload } = useApp()
  const { t } = useTranslation()
  const streamError = streaming?.streamError
  if (!globalError && !streamError) return null
  return (
    <div className="space-y-2 mb-4" data-testid="error-banners">
      {globalError && (
        <div role="alert" className="flex items-center gap-3 rounded-control border border-bad/30 bg-bad/10 px-4 py-2 text-sm text-bad-ink">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
          <span className="flex-1 break-words">{t('app.global_error', { error: globalError })}</span>
          <button type="button" onClick={() => reload()} className="font-semibold underline hover:text-bad-ink">
            {t('common.retry')}
          </button>
          <button
            type="button"
            onClick={() => dispatch({ type: 'SET_GLOBAL_ERROR', payload: null })}
            aria-label={t('common.dismiss')}
            className="text-bad hover:text-bad-ink"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
      {streamError && (
        <div role="alert" className="flex items-center gap-3 rounded-control border border-warn/30 bg-warn/10 px-4 py-2 text-sm text-warn-ink">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
          <span className="flex-1 break-words">{t('app.stream_error', { error: streamError })}</span>
        </div>
      )}
    </div>
  )
}

// -----------------------------------------------------------------------
// Inner app — needs access to context
// -----------------------------------------------------------------------

function AppShell() {
  const { agentStatus, streaming } = useApp()
  const { t } = useTranslation()
  const [drawerOpen, setDrawerOpen] = useState(false)

  const agentRunning = agentStatus?.running === true
  const streamActive = streaming?.isStreaming === true

  // Close the mobile drawer on Escape (and on nav click, below).
  useEffect(() => {
    if (!drawerOpen) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setDrawerOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawerOpen])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg md:flex-row">
      {/* ===================== Mobile top bar (< md) ===================== */}
      <header className="flex flex-shrink-0 items-center justify-between border-b border-line bg-panel px-4 py-2.5 md:hidden">
        <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-card bg-panel ring-1 ring-line">
          <img src="/assets/logo_web.png" alt="" className="h-full w-full object-cover" />
        </div>
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label={t('nav.open_menu')}
          aria-expanded={drawerOpen}
          className="btn-ghost !px-2 !py-2"
        >
          <Menu className="h-5 w-5" />
        </button>
      </header>

      {drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* ===================== Sidebar (drawer below md) ===================== */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-64 max-w-[85vw] flex-shrink-0 flex-col border-r border-line bg-panel shadow-lg transition-transform duration-200 md:static md:z-auto md:max-w-none md:translate-x-0 md:shadow-md ${
          drawerOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Logo */}
        <div className="flex items-center justify-between border-b border-line p-6">
          <div className="flex items-center space-x-3">
            <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-card bg-panel shadow-sm ring-1 ring-line">
              <img src="/assets/logo_web.png" alt="HiMe Logo" className="h-full w-full object-cover" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-ink">HiMe</h1>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-primary-600">{t('nav.brand_subtitle')}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            aria-label={t('nav.close_menu')}
            className="btn-ghost !px-1.5 !py-1.5 md:hidden"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Data source badge */}
        <div className="flex items-center justify-between gap-2 px-4 pb-2 pt-4">
          <span className="inline-flex items-center rounded-control bg-ok/15 px-2.5 py-1 text-xs font-semibold text-ok-ink">
            {t('nav.live_healthkit')}
          </span>
          <LanguageSwitcher />
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 overflow-y-auto px-4 py-4">
          {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              onClick={() => setDrawerOpen(false)}
              className={({ isActive }) =>
                `flex items-center space-x-3 rounded-control px-3 py-2 text-sm font-medium transition-colors ${isActive
                  ? 'bg-primary-50 text-primary-700'
                  : 'text-ink-2 hover:bg-sunken hover:text-ink'
                }`
              }
            >
              <Icon className="h-5 w-5 flex-shrink-0" />
              <span>{t(labelKey)}</span>
              {labelKey === 'nav.agent_monitor' && agentRunning && (
                <span className="ml-auto h-2 w-2 animate-pulse rounded-full bg-ok" />
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="space-y-3 border-t border-line p-4">
          <ThemeToggle />
          <div className="flex flex-col gap-1 text-xs text-ink-3">
            <div className="flex items-center space-x-2">
              <HardDrive className="h-3 w-3" />
              <span className="truncate capitalize">{t('nav.live_healthkit')}</span>
            </div>
            {(agentRunning || streamActive) && (
              <div className="flex items-center gap-3">
                {agentRunning && (
                  <span className="flex items-center gap-1 text-ok-ink">
                    <Database className="h-3 w-3" />
                    {t('nav.agent_indicator')}
                  </span>
                )}
                {streamActive && (
                  <span className="flex items-center gap-1 text-primary-600">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary-500" />
                    {t('nav.stream_indicator')}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </aside>

      {/* ===================== Content ===================== */}
      <main className="min-w-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl p-4 md:p-8">
          <ErrorBanners />
          <Routes>
            <Route path="*" element={<PersistentViews />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

// -----------------------------------------------------------------------
// Root
// -----------------------------------------------------------------------

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        {/* Sits above everything: shows only when the backend rejects a call
            with 401/403 so the user can supply the API token. */}
        <AuthTokenPrompt />
        <AppProvider>
          <AppShell />
        </AppProvider>
      </ErrorBoundary>
    </BrowserRouter>
  )
}
