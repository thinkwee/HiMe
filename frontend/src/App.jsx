import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  Activity, BarChart3, Bot, FileText, Database, HardDrive, MessageSquare, AppWindow, Sparkles
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { AppProvider, useApp } from './context/AppContext'
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
// Persistent views — all four pages stay mounted, only visibility toggles.
// Prevents WebSocket/stream/state loss when switching pages.
// -----------------------------------------------------------------------

function PersistentViews() {
  const location = useLocation()
  const path = location.pathname

  return (
    <>
      <div className={path === '/' ? 'block' : 'hidden'}>
        <Dashboard />
      </div>
      <div className={path === '/agent' ? 'block' : 'hidden'}>
        <AutonomousAgentMonitor />
      </div>
      <div className={path === '/reports' ? 'block' : 'hidden'}>
        <ReportsView />
      </div>
      <div className={path === '/prompts' ? 'block' : 'hidden'}>
        <PromptEditor />
      </div>
      <div className={path === '/skills' ? 'block' : 'hidden'}>
        <Skills />
      </div>
      <div className={path === '/knowledge' ? 'block' : 'hidden'}>
        <KnowledgeBase />
      </div>
      <div className={path === '/pages' ? 'block' : 'hidden'}>
        <PersonalisedPages />
      </div>
    </>
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
        <AppProvider>
          <AppShell />
        </AppProvider>
      </ErrorBoundary>
    </BrowserRouter>
  )
}
