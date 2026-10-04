/**
 * AuthTokenPrompt — minimal recovery UI for a token-protected backend.
 *
 * The dashboard is a static bundle: in Docker it is built once and served to
 * everyone, so the API_AUTH_TOKEN cannot be compiled in (and must not be —
 * dist/ is public to anyone who can load the page). Instead api.js reads the
 * token from browser storage at request time, and this component is what puts
 * it there: whenever any /api call comes back 401/403 it asks the user to
 * paste the token, stores it, and reloads.
 *
 * Not an authentication system — there are no accounts and no sessions, just
 * the one shared bearer token from the server's .env.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, X } from 'lucide-react'

import { onAuthRequired, reloadForAuth, setAuthToken } from '../lib/api'

export default function AuthTokenPrompt() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [token, setToken] = useState('')
  const [remember, setRemember] = useState(false)
  // Once any call has been rejected the page is effectively unauthenticated.
  // `needed` keeps a small persistent banner visible after the dialog is
  // dismissed, so the user can always get back to the prompt.
  const [needed, setNeeded] = useState(false)
  // Once dismissed, don't pop the modal again until the next page load —
  // otherwise the stream's auto-reconnect would re-open it every few seconds.
  const dismissedRef = useRef(false)

  useEffect(() => {
    if (typeof onAuthRequired !== 'function') return undefined
    return onAuthRequired(() => {
      setNeeded(true)
      if (!dismissedRef.current) setOpen(true)
    })
  }, [])

  if (!open) {
    if (!needed) return null
    return (
      <div
        role="alert"
        className="fixed top-0 inset-x-0 z-[90] flex items-center justify-center gap-3 bg-warn/15 text-warn-ink text-sm px-4 py-2 shadow"
      >
        <KeyRound className="w-4 h-4" aria-hidden="true" />
        <span>{t('auth.banner')}</span>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="font-semibold underline hover:text-warn-ink"
        >
          {t('auth.enter_token')}
        </button>
      </div>
    )
  }

  const dismiss = () => {
    dismissedRef.current = true
    setOpen(false)
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') dismiss()
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    const value = token.trim()
    if (!value) return
    setAuthToken(value, { remember })
    reloadForAuth()
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-token-title"
      onKeyDown={onKeyDown}
    >
      <div className="bg-panel rounded-card shadow-2xl w-full max-w-md overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 bg-warn/10 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <KeyRound className="w-6 h-6 text-warn" aria-hidden="true" />
            <h3 id="auth-token-title" className="section-title">
              {t('auth.title')}
            </h3>
          </div>
          <button
            type="button"
            onClick={dismiss}
            aria-label={t('common.close')}
            className="text-ink-3 hover:text-ink-2 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          <p className="text-sm text-ink-2">{t('auth.description')}</p>

          <div className="space-y-1.5">
            <label htmlFor="auth-token-input" className="block text-sm font-medium text-ink">
              {t('auth.token_label')}
            </label>
            <input
              id="auth-token-input"
              type="password"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={t('auth.token_placeholder')}
              className="w-full px-3 py-2 border border-line-2 rounded-control text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-300 focus:border-primary-400"
            />
          </div>

          <label className="flex items-start gap-2 text-sm text-ink-2">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="mt-0.5 rounded-chip border-line-2 text-primary-600 focus:ring-primary-300"
            />
            <span>
              {t('auth.remember')}
              <span className="block text-xs text-ink-3">{t('auth.remember_hint')}</span>
            </span>
          </label>

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={dismiss}
              className="px-3 py-2 text-sm font-medium text-ink-2 hover:text-ink transition-colors"
            >
              {t('common.dismiss')}
            </button>
            <button
              type="submit"
              disabled={!token.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-control hover:bg-primary-600/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {t('auth.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
