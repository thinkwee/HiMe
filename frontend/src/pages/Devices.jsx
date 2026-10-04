import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Watch,
  Wifi,
  WifiOff,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertCircle,
  AlertTriangle,
  ExternalLink,
  Link2,
  Radio,
  Info,
} from 'lucide-react'
import { api } from '../lib/api.js'
import { formatFullDateTime } from '../lib/utils'

const STATUS_POLL_MS = 30000

/** Normalise a provider entry's identifier the same way the backend does
 * (see `routes.py`: `p.get("provider") or p.get("id") or p.get("name")`).
 * open-wearables provider objects carry the canonical slug in `provider`
 * (there is no `id` field); `name` is a display name like "Google Health"
 * and must never be used as the connect-call/matching key on its own, or
 * multi-word providers produce keys like "google health" that 422 upstream. */
function providerKey(p) {
  return String(p?.provider || p?.id || p?.name || '').toLowerCase()
}

function providerLabel(p) {
  return p?.display_name || p?.name || p?.provider || p?.id || ''
}

/** Connection entries come straight from the open-wearables service, whose
 * exact field name for "which provider" isn't nailed down on this side —
 * check every plausible key rather than assuming one. Must stay in sync
 * with `providerKey` above (both prefer `provider`) so the "already
 * connected" lookup actually matches. */
function connectionKey(c) {
  return String(c?.provider || c?.provider_id || c?.provider_name || c?.name || '').toLowerCase()
}

/** Defensively stringify a status field that *should* be a string or absent,
 * but whose backend shape isn't fully pinned down (e.g. `last_poll_error` is
 * a dict of `{category: message}` from the poller, not a plain string) and
 * may keep evolving as the sync/reconciliation rework lands server-side.
 * Never render a raw object as a React child - format it or drop it. */
function formatStatusValue(value) {
  if (value == null) return null
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (typeof value === 'object') {
    const lines = Object.entries(value).map(([category, message]) => `${category}: ${formatStatusValue(message) ?? ''}`)
    return lines.length ? lines.join('; ') : null
  }
  return String(value)
}

export default function Devices() {
  const { t } = useTranslation()

  const [status, setStatus] = useState(null)
  const [statusLoading, setStatusLoading] = useState(true)
  const [statusError, setStatusError] = useState(null)

  const [providers, setProviders] = useState([])
  const [providersLoading, setProvidersLoading] = useState(false)
  const [providersError, setProvidersError] = useState(null)
  const providersFetchedRef = useRef(false)

  const [connectingKey, setConnectingKey] = useState(null)
  const [connectError, setConnectError] = useState(null)

  const [syncing, setSyncing] = useState(false)
  const [syncFeedback, setSyncFeedback] = useState(null) // { type, message }

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.getOpenWearablesStatus()
      if (res && res.success === false) {
        setStatusError(res.error || t('devices.status_unavailable_body'))
        setStatus(null)
      } else {
        setStatusError(null)
        setStatus(res)
      }
    } catch (err) {
      setStatusError(err?.message || t('common.network_error'))
      setStatus(null)
    } finally {
      setStatusLoading(false)
    }
  }, [t])

  const fetchProviders = useCallback(async () => {
    setProvidersLoading(true)
    setProvidersError(null)
    try {
      const res = await api.getOpenWearablesProviders()
      if (res && res.success === false) {
        setProvidersError(res.error || t('devices.providers_error'))
        setProviders([])
      } else {
        setProviders(Array.isArray(res?.providers) ? res.providers : [])
      }
    } catch (err) {
      setProvidersError(err?.message || t('common.network_error'))
      setProviders([])
    } finally {
      setProvidersLoading(false)
    }
  }, [t])

  // Initial load + poll /status every 30s while this page stays mounted.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchStatus()
    const interval = setInterval(fetchStatus, STATUS_POLL_MS)
    return () => clearInterval(interval)
  }, [fetchStatus])

  // Providers only matter once the integration is enabled — fetch lazily the
  // first time we learn that, rather than on every 30s status poll.
  useEffect(() => {
    if (status?.enabled && !providersFetchedRef.current) {
      providersFetchedRef.current = true
      fetchProviders()
    }
  }, [status?.enabled, fetchProviders])

  const connectedKeys = new Set((status?.connections || []).map(connectionKey))

  const handleConnect = async (provider) => {
    const key = providerKey(provider)
    setConnectingKey(key)
    setConnectError(null)
    try {
      const res = await api.connectOpenWearablesProvider(key)
      if (res && res.success === false) {
        setConnectError(res.error || t('devices.connect_error_generic'))
      } else if (res?.authorization_url) {
        window.open(res.authorization_url, '_blank', 'noopener,noreferrer')
      } else {
        setConnectError(t('devices.connect_error_generic'))
      }
    } catch (err) {
      setConnectError(err?.message || t('common.network_error'))
    } finally {
      setConnectingKey(null)
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    setSyncFeedback(null)
    try {
      const res = await api.syncOpenWearables()
      if (res && res.success === false) {
        setSyncFeedback({ type: 'error', message: res.error || t('devices.sync_error_generic') })
      } else {
        setSyncFeedback({ type: 'success', message: t('devices.sync_success') })
        await fetchStatus()
      }
    } catch (err) {
      setSyncFeedback({ type: 'error', message: err?.message || t('common.network_error') })
    } finally {
      setSyncing(false)
      setTimeout(() => setSyncFeedback(null), 5000)
    }
  }

  const enabled = status?.enabled === true
  const reachable = status?.reachable === true
  const canSync = enabled && reachable && !!status?.ow_user_id

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-4xl font-extrabold text-gray-900 tracking-tight">{t('devices.title')}</h2>
          <p className="mt-2 text-base text-gray-500 max-w-2xl">{t('devices.subtitle')}</p>
        </div>
        <button
          onClick={fetchStatus}
          disabled={statusLoading}
          className="btn btn-secondary flex items-center gap-2 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${statusLoading ? 'animate-spin' : ''}`} />
          <span className="text-sm font-bold">{t('common.refresh')}</span>
        </button>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Status card                                                       */}
      {/* ---------------------------------------------------------------- */}

      {statusLoading && !status && !statusError ? (
        <div className="card flex items-center justify-center py-16">
          <RefreshCw className="w-6 h-6 text-primary-500 animate-spin mr-3" />
          <span className="text-gray-500 font-medium">{t('devices.status_loading')}</span>
        </div>
      ) : statusError ? (
        <div className="card border-red-200 bg-red-50/50">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-6 h-6 text-red-500 flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-bold text-red-800">{t('devices.status_unavailable_title')}</h3>
              <p className="text-sm text-red-700 mt-1">{statusError}</p>
              <button
                onClick={fetchStatus}
                className="mt-3 text-sm font-bold text-red-700 hover:text-red-900 underline"
              >
                {t('common.retry')}
              </button>
            </div>
          </div>
        </div>
      ) : !enabled ? (
        <div className="card border-primary-100 bg-gradient-to-br from-primary-50 to-white">
          <div className="flex items-start gap-4">
            <div className="p-3 bg-white rounded-xl shadow-sm ring-1 ring-primary-100 flex-shrink-0">
              <Watch className="w-6 h-6 text-primary-500" />
            </div>
            <div className="space-y-3">
              <div>
                <h3 className="font-bold text-lg text-gray-900">{t('devices.disabled_title')}</h3>
                <p className="text-sm text-gray-600 mt-1 max-w-2xl">{t('devices.disabled_body')}</p>
              </div>
              <div className="bg-white/70 border border-primary-100 rounded-xl px-4 py-3 text-sm text-gray-700">
                <p>
                  {t('devices.disabled_setup_prefix')}{' '}
                  <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">docs/OPEN_WEARABLES.md</code>
                  {' '}{t('devices.disabled_setup_middle')}{' '}
                  <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">./hime.sh wearables setup</code>.
                </p>
              </div>
              <p className="text-xs text-gray-500 flex items-center gap-1.5">
                <Info className="w-3.5 h-3.5 flex-shrink-0" />
                {t('devices.disabled_apple_note')}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-bold text-lg text-gray-900 flex items-center gap-2">
              <Watch className="w-5 h-5 text-primary-500" />
              {t('devices.status_title')}
            </h3>
            <span
              className={`inline-flex items-center gap-1.5 px-3 py-1 text-xs font-bold rounded-full ${
                reachable ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-700'
              }`}
            >
              {reachable ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
              {reachable ? t('devices.reachable_yes') : t('devices.reachable_no')}
            </span>
          </div>

          {!reachable && (
            <div className="mb-4 flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-800">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{t('devices.unreachable_hint')}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-gray-50 rounded-xl p-4">
              <div className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">
                {t('devices.ow_user_label')}
              </div>
              <div className="text-sm font-mono font-bold text-gray-800 truncate">
                {formatStatusValue(status?.ow_user_id) || t('devices.ow_user_pending')}
              </div>
            </div>

            <div className="bg-gray-50 rounded-xl p-4">
              <div className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">
                {t('devices.last_poll_label')}
              </div>
              <div className="text-sm font-bold text-gray-800">
                {status?.last_poll_at ? formatFullDateTime(status.last_poll_at) : t('devices.last_poll_never')}
              </div>
              {formatStatusValue(status?.last_poll_status) && (
                <div
                  className={`text-xs font-semibold mt-1 flex items-center gap-1 ${
                    status.last_poll_status === 'error' ? 'text-red-600' : 'text-green-600'
                  }`}
                >
                  {status.last_poll_status === 'error' ? (
                    <XCircle className="w-3 h-3" />
                  ) : (
                    <CheckCircle2 className="w-3 h-3" />
                  )}
                  {formatStatusValue(status.last_poll_status)}
                </div>
              )}
              {formatStatusValue(status?.last_poll_error) && (
                <div
                  className="text-[11px] text-red-500 mt-1 truncate"
                  title={formatStatusValue(status.last_poll_error)}
                >
                  {t('devices.last_poll_error_prefix')} {formatStatusValue(status.last_poll_error)}
                </div>
              )}
            </div>

            <div className="bg-gray-50 rounded-xl p-4">
              <div className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">
                {t('devices.webhook_label')}
              </div>
              <div
                className={`text-sm font-bold flex items-center gap-1.5 ${
                  status?.webhook_registered ? 'text-green-700' : 'text-gray-500'
                }`}
              >
                <Radio className="w-3.5 h-3.5" />
                {status?.webhook_registered ? t('devices.webhook_registered') : t('devices.webhook_not_registered')}
              </div>
              {formatStatusValue(status?.webhook_url) && (
                <div
                  className="text-[11px] text-gray-400 mt-1 truncate"
                  title={formatStatusValue(status.webhook_url)}
                >
                  {formatStatusValue(status.webhook_url)}
                </div>
              )}
            </div>

            <div className="bg-gray-50 rounded-xl p-4">
              <div className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">
                {t('devices.connections_label')}
              </div>
              <div className="text-2xl font-black text-gray-900">{(status?.connections || []).length}</div>
            </div>
          </div>
        </div>
      )}

      {/* ---------------------------------------------------------------- */}
      {/* Providers grid                                                    */}
      {/* ---------------------------------------------------------------- */}

      {enabled && !statusError && (
        <div className="card">
          <div className="mb-4">
            <h3 className="font-bold text-lg text-gray-900">{t('devices.providers_title')}</h3>
            <p className="text-sm text-gray-500 mt-1">{t('devices.providers_subtitle')}</p>
          </div>

          {connectError && (
            <div className="mb-4 flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{connectError}</span>
            </div>
          )}

          {providersLoading ? (
            <div className="flex items-center justify-center py-12">
              <RefreshCw className="w-5 h-5 text-primary-500 animate-spin mr-3" />
              <span className="text-sm text-gray-500 font-medium">{t('devices.providers_loading')}</span>
            </div>
          ) : providersError ? (
            <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{providersError}</span>
            </div>
          ) : providers.length === 0 ? (
            <div className="p-6 bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200 text-center">
              <Watch className="w-8 h-8 text-gray-300 mx-auto mb-2" />
              <p className="text-sm text-gray-500">{t('devices.providers_empty')}</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {providers.map((p) => {
                const key = providerKey(p)
                const isConnected = connectedKeys.has(key)
                const requiresHttps = p?.requires_public_https === true || key === 'garmin'
                const isConnecting = connectingKey === key
                return (
                  <div
                    key={key || providerLabel(p)}
                    className="border border-gray-200 rounded-xl p-4 flex flex-col gap-3 hover:border-primary-200 hover:shadow-sm transition-all"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-gray-900 capitalize">{providerLabel(p)}</span>
                      {isConnected && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-black uppercase tracking-wider text-green-700 bg-green-100 px-2 py-1 rounded-full">
                          <CheckCircle2 className="w-3 h-3" />
                          {t('devices.provider_connected')}
                        </span>
                      )}
                    </div>

                    {requiresHttps && (
                      <div className="flex items-start gap-1.5 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-2">
                        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                        <span>{t('devices.provider_requires_https_note')}</span>
                      </div>
                    )}

                    <button
                      onClick={() => handleConnect(p)}
                      disabled={isConnecting || !reachable}
                      className="btn btn-secondary flex items-center justify-center gap-2 text-sm disabled:opacity-50 mt-auto"
                    >
                      {isConnecting ? (
                        <RefreshCw className="w-4 h-4 animate-spin" />
                      ) : (
                        <Link2 className="w-4 h-4" />
                      )}
                      <span className="font-bold">
                        {isConnecting
                          ? t('devices.provider_connecting')
                          : isConnected
                          ? t('devices.provider_reconnect')
                          : t('devices.provider_connect')}
                      </span>
                      {!isConnecting && <ExternalLink className="w-3.5 h-3.5 opacity-60" />}
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ---------------------------------------------------------------- */}
      {/* Sync controls                                                     */}
      {/* ---------------------------------------------------------------- */}

      {enabled && !statusError && (
        <div className="card">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <h3 className="font-bold text-lg text-gray-900">{t('devices.sync_title')}</h3>
              <p className="text-sm text-gray-500 mt-1 max-w-xl">{t('devices.sync_subtitle')}</p>
              {!canSync && (
                <p className="text-xs text-amber-600 mt-1.5">{t('devices.sync_disabled_hint')}</p>
              )}
            </div>
            <div className="flex items-center gap-3">
              {syncFeedback && (
                <div
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold ${
                    syncFeedback.type === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                  }`}
                >
                  {syncFeedback.type === 'success' ? (
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  ) : (
                    <AlertCircle className="w-3.5 h-3.5" />
                  )}
                  <span>{syncFeedback.message}</span>
                </div>
              )}
              <button
                onClick={handleSync}
                disabled={syncing || !canSync}
                className="btn btn-primary flex items-center gap-2 disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />
                <span className="font-bold">{syncing ? t('devices.sync_button_busy') : t('devices.sync_button')}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
