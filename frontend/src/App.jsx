import { memo } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, FileText, Database, HardDrive, MessageSquare, AppWindow, Sparkles, X
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { AppProvider, useApp } from './context/AppContext'
import AuthTokenPrompt from './components/AuthTokenPrompt'
import ErrorBoundary from './components/ErrorBoundary'
import LanguageSwitcher from './components/LanguageSwitcher'
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
        <div role="alert" className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
          <span className="flex-1 break-words">{t('app.global_error', { error: globalError })}</span>
          <button type="button" onClick={() => reload()} className="font-semibold underline hover:text-red-900">
            {t('common.retry')}
          </button>
          <button
            type="button"
            onClick={() => dispatch({ type: 'SET_GLOBAL_ERROR', payload: null })}
            aria-label={t('common.dismiss')}
            className="text-red-400 hover:text-red-700"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
      {streamError && (
        <div role="alert" className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
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

  const agentRunning = agentStatus?.running === true
  const streamActive = streaming?.isStreaming === true

  return (
    <div className="flex h-screen bg-hime-warm overflow-hidden">
      {/* ===================== Sidebar ===================== */}
      <aside className="w-64 bg-white shadow-md flex flex-col flex-shrink-0">
        {/* Logo */}
        <div className="p-6 border-b border-gray-200">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center overflow-hidden shadow-sm ring-1 ring-gray-200 bg-white">
              <img src="/assets/logo_web.png" alt="HiMe Logo" className="w-full h-full object-cover" />
            </div>
            <div>
              <h1 className="font-bold text-gray-900 tracking-tight text-lg">HiMe</h1>
              <p className="text-[10px] uppercase font-semibold text-primary-600 tracking-wider">{t('nav.brand_subtitle')}</p>
            </div>
          </div>
        </div>

        {/* Data source badge */}
        <div className="px-4 pt-4 pb-2 flex items-center justify-between gap-2">
          <span className="inline-flex items-center px-2.5 py-1 text-xs font-semibold bg-green-100 text-green-800 rounded-lg">
            {t('nav.live_healthkit')}
          </span>
          <LanguageSwitcher />
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center space-x-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isActive
                  ? 'bg-primary-50 text-primary-700'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                }`
              }
            >
              <Icon className="w-5 h-5 flex-shrink-0" />
              <span>{t(labelKey)}</span>
              {labelKey === 'nav.agent_monitor' && agentRunning && (
                <span className="ml-auto w-2 h-2 bg-green-500 rounded-full animate-pulse" />
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200">
          <div className="flex flex-col gap-1 text-xs text-gray-400">
            <div className="flex items-center space-x-2">
              <HardDrive className="w-3 h-3" />
              <span className="truncate capitalize">{t('nav.live_healthkit')}</span>
            </div>
            {(agentRunning || streamActive) && (
              <div className="flex items-center gap-3">
                {agentRunning && (
                  <span className="flex items-center gap-1 text-green-600">
                    <Database className="w-3 h-3" />
                    {t('nav.agent_indicator')}
                  </span>
                )}
                {streamActive && (
                  <span className="flex items-center gap-1 text-primary-600">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary-500 animate-pulse" />
                    {t('nav.stream_indicator')}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </aside>

      {/* ===================== Content ===================== */}
      <main className="flex-1 overflow-auto">
        <div className="p-8 max-w-7xl mx-auto">
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
