import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import StatisticsPanel from '../components/StatisticsPanel'
import { api } from '../lib/api'
import { useApp } from '../context/AppContext'

export default function Dashboard({ active = true }) {
  const { t } = useTranslation()
  const {
    streaming,
    reconnectStream,
  } = useApp()

  const { isStreaming, liveHistoryWindow } = streaming

  // While the dashboard is hidden (another route is active) keep showing the
  // last visible snapshot: StatisticsPanel is memoised on its props, so live
  // batches don't trigger chart re-renders nobody can see. On return the
  // snapshot catches up in one step.
  const live = {
    streamData: streaming.streamData,
    historicalData: streaming.historicalData,
    liveHistoryWindow,
  }
  const [snapshot, setSnapshot] = useState(live)
  if (active && Object.keys(live).some((k) => snapshot[k] !== live[k])) setSnapshot(live)
  const view = active ? live : snapshot

  const [featureMetadata, setFeatureMetadata] = useState({})

  // Load feature metadata
  useEffect(() => {
    api.getFeatureMetadata().then(r => {
      if (r?.success && r.features) setFeatureMetadata(r.features)
    }).catch((err) => console.warn('Failed to load feature metadata:', err))
  }, [])

  /** Switch time window: reconnects the stream with the new window. */
  const setLiveHistoryWindow = useCallback((v) => {
    const newWindow = typeof v === 'function' ? v(liveHistoryWindow) : v
    if (newWindow !== liveHistoryWindow) {
      reconnectStream(newWindow)
    }
  }, [liveHistoryWindow, reconnectStream])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="page-title">{t('dashboard.title')}</h2>
          <p className="page-subtitle">
            {t('dashboard.subtitle')}
            <span className="ml-3 px-2.5 py-1 text-sm font-semibold bg-ok/15 text-ok-ink rounded-control">
              {t('dashboard.live_badge')}
            </span>
            {isStreaming && (
              <span className="ml-2 px-2.5 py-1 text-sm font-semibold bg-info/15 text-info-ink rounded-control animate-pulse">
                {t('dashboard.streaming_badge')}
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Data Overview - unified 4-card row with time window + stats */}
      <div className="w-full">
        <StatisticsPanel
          data={view.streamData}
          historicalData={view.historicalData}
          featureMetadata={featureMetadata}
          liveHistoryWindow={view.liveHistoryWindow}
          setLiveHistoryWindow={setLiveHistoryWindow}
        />
      </div>
    </div>
  )
}
