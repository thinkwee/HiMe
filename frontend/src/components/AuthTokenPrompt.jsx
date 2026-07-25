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
  // Once dismissed, stay quiet until the next page load — otherwise the
  // stream's auto-reconnect would re-open the dialog every few seconds.
  const dismissedRef = useRef(false)

  useEffect(() => {
    if (typeof onAuthRequired !== 'function') return undefined
    return onAuthRequired(() => {
      if (!dismissedRef.current) setOpen(true)
    })
  }, [])

  if (!open) return null

  const dismiss = () => {
    dismissedRef.current = true
    setOpen(false)
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
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 bg-amber-50 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <KeyRound className="w-6 h-6 text-amber-500" aria-hidden="true" />
            <h3 id="auth-token-title" className="text-lg font-semibold text-gray-900">
              {t('auth.title')}
            </h3>
          </div>
          <button
            type="button"
            onClick={dismiss}
            aria-label={t('common.close')}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          <p className="text-sm text-gray-600">{t('auth.description')}</p>

          <div className="space-y-1.5">
            <label htmlFor="auth-token-input" className="block text-sm font-medium text-gray-700">
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
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-300 focus:border-primary-400"
            />
          </div>

          <label className="flex items-start gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="mt-0.5 rounded border-gray-300 text-primary-600 focus:ring-primary-300"
            />
            <span>
              {t('auth.remember')}
              <span className="block text-xs text-gray-400">{t('auth.remember_hint')}</span>
            </span>
          </label>

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={dismiss}
              className="px-3 py-2 text-sm font-medium text-gray-600 hover:text-gray-900 transition-colors"
            >
              {t('common.dismiss')}
            </button>
            <button
              type="submit"
              disabled={!token.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {t('auth.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
