import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import StatisticsPanel from '../components/StatisticsPanel'
import { api } from '../lib/api'
import { useApp } from '../context/AppContext'

export default function Dashboard() {
  const { t } = useTranslation()
  const {
    streaming,
    reconnectStream,
  } = useApp()

  const {
    isStreaming,
    streamData,
    historicalData,
    liveHistoryWindow,
  } = streaming

  const [featureMetadata, setFeatureMetadata] = useState({})

  // Load feature metadata
  useEffect(() => {
    api.getFeatureMetadata().then(r => {
      if (r?.success && r.features) setFeatureMetadata(r.features)
    }).catch((err) => console.warn('Failed to load feature metadata:', err))
  }, [])

  /** Switch time window: reconnects the stream with the new window. */
  const setLiveHistoryWindow = (v) => {
    const newWindow = typeof v === 'function' ? v(liveHistoryWindow) : v
    if (newWindow !== liveHistoryWindow) {
      reconnectStream(newWindow)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-4xl font-extrabold text-gray-900 tracking-tight">{t('dashboard.title')}</h2>
          <p className="mt-2 text-base text-gray-500">
            {t('dashboard.subtitle')}
            <span className="ml-3 px-2.5 py-1 text-sm font-semibold bg-green-100 text-green-800 rounded-lg">
              {t('dashboard.live_badge')}
            </span>
            {isStreaming && (
              <span className="ml-2 px-2.5 py-1 text-sm font-semibold bg-blue-100 text-blue-800 rounded-lg animate-pulse">
                {t('dashboard.streaming_badge')}
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Data Overview - unified 4-card row with time window + stats */}
      <div className="w-full">
        <StatisticsPanel
          data={streamData}
          historicalData={historicalData}
          featureMetadata={featureMetadata}
          liveHistoryWindow={liveHistoryWindow}
          setLiveHistoryWindow={setLiveHistoryWindow}
          isStreaming={isStreaming}
        />
      </div>
    </div>
  )
}
