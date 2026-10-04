import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceDot } from 'recharts'
import { TrendingUp, Users, BarChart3, Database, Calendar, Activity, Heart, Moon, Zap, Footprints, Dumbbell, X, Copy, Check } from 'lucide-react'
import { memo, useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { toPng } from 'html-to-image'
import { api } from '../lib/api'
import { formatFullDateTime, parseBackendDate } from '../lib/utils'
import i18n from '../i18n'
import ErrorBoundary from './ErrorBoundary'
import { useChartTheme } from '../lib/chartColors'

const ENUMERATED_METRICS = {
  // Activity & Fitness
  'Active Energy': 'Active Energy (kcal)',
  'Cycling Cadence': 'Cycling Cadence (RPM)',
  'Cycling Power': 'Cycling Power (W)',
  'Cycling Speed': 'Cycling Speed (m/s)',
  'Distance': 'Distance (km)',
  'Exercise Time': 'Exercise Time (min)',
  'Flights Climbed': 'Flights Climbed (Count)',
  'Resting Energy': 'Resting Energy (kcal)',
  'Running Power': 'Running Power (W)',
  'Stand Time': 'Stand Time (min)',
  'Steps': 'Steps (Count)',
  'Walking Step Length': 'Step Length (cm)',

  // Heart & Vitals
  'Atrial Fibrillation Burden': 'Atrial Fibrillation Burden (%)',
  'Blood Oxygen': 'Blood Oxygen (%)',
  'Heart Rate': 'Heart Rate (BPM)',
  'Heart Rate Recovery': 'Heart Rate Recovery (BPM)',
  'Heart Rate Variability': 'Heart Rate Variability (ms)',
  'High Heart Rate Event': 'High Heart Rate Event (BPM)',
  'Irregular Heart Rhythm Event': 'Irregular Heart Rhythm Event (Count)',
  'Low Heart Rate Event': 'Low Heart Rate Event (BPM)',
  'Respiratory Rate': 'Respiratory Rate (br/min)',
  'Resting Heart Rate': 'Resting Heart Rate (BPM)',
  'Vo2max': 'VO2 Max (mL/kg·min)',
  'Walking Heart Rate Average': 'Walking Heart Rate (BPM)',
  'Walking Heart Rate Avg': 'Walking Heart Rate (BPM)',

  // Sleep & Mindfulness
  'Mindful Session': 'Mindful Session (min)',
  'Sleep Asleep': 'Sleep Asleep (min)',
  'Sleep Awake': 'Sleep Awake (min)',
  'Sleep Core': 'Sleep Core (min)',
  'Sleep Deep': 'Sleep Deep (min)',
  'Sleep In Bed': 'Sleep In Bed (min)',
  'Sleep Rem': 'Sleep Rem (min)',
  'Sleeping Wrist Temp': 'Sleeping Wrist Temp (°C)',

  // Mobility & Gait
  'Running Vertical Oscillation': 'Running Vertical Oscillation (cm)',
  'Stair Ascent Speed': 'Stair Ascent Speed (m/s)',
  'Stair Descent Speed': 'Stair Descent Speed (m/s)',
  'Walking Asymmetry': 'Walking Asymmetry (%)',
  'Walking Double Support': 'Walking Double Support (%)',
  'Walking Speed': 'Walking Speed (m/s)',
  'Walking Steadiness': 'Walking Steadiness (%)',

  // Workouts
  'Workout Running Duration': 'Running Duration (min)',
  'Workout Running Distance': 'Running Distance (km)',
  'Workout Running Energy': 'Running Energy (kcal)',
  'Workout Cycling Duration': 'Cycling Duration (min)',
  'Workout Cycling Distance': 'Cycling Distance (km)',
  'Workout Cycling Energy': 'Cycling Energy (kcal)',
  'Workout Swimming Duration': 'Swimming Duration (min)',
  'Workout Swimming Distance': 'Swimming Distance (km)',
  'Workout Swimming Energy': 'Swimming Energy (kcal)',
  'Workout Walking Duration': 'Walking Duration (min)',
  'Workout Walking Distance': 'Walking Distance (km)',
  'Workout Walking Energy': 'Walking Energy (kcal)',
  'Workout Hiking Duration': 'Hiking Duration (min)',
  'Workout Hiking Distance': 'Hiking Distance (km)',
  'Workout Hiking Energy': 'Hiking Energy (kcal)',
  'Workout Yoga Duration': 'Yoga Duration (min)',
  'Workout Yoga Energy': 'Yoga Energy (kcal)',
  'Workout Strength Duration': 'Strength Duration (min)',
  'Workout Strength Energy': 'Strength Energy (kcal)',
  'Workout Hiit Duration': 'HIIT Duration (min)',
  'Workout Hiit Energy': 'HIIT Energy (kcal)',
  'Workout Elliptical Duration': 'Elliptical Duration (min)',
  'Workout Elliptical Distance': 'Elliptical Distance (km)',
  'Workout Elliptical Energy': 'Elliptical Energy (kcal)',
  'Workout Rowing Duration': 'Rowing Duration (min)',
  'Workout Rowing Distance': 'Rowing Distance (km)',
  'Workout Rowing Energy': 'Rowing Energy (kcal)',
  'Workout Core Duration': 'Core Duration (min)',
  'Workout Core Energy': 'Core Energy (kcal)',
  'Workout Flexibility Duration': 'Flexibility Duration (min)',
  'Workout Cooldown Duration': 'Cooldown Duration (min)',

  // Environment & Nutrition
  'Audio Exposure Event': 'Audio Exposure Event (Count)',
  'Uv Index': 'UV Index (Index)',
  'Water': 'Water (ml)',

  // Body & Wellness
  'Body Mass': 'Body Mass (kg)',
  'Body Mass Index': 'Body Mass Index (Index)',
  'Running Ground Contact': 'Running Ground Contact (ms)',
  'Running Stride Length': 'Running Stride Length (cm)',
  'Six Minute Walk': 'Six Minute Walk (m)',
  'Time In Daylight': 'Time In Daylight (min)'
};

const getFriendlyName = (feature, meta = {}) => {
  if (!feature) return i18n.t('statistics.unknown_metric');

  // 1. Get the standard cleaned name first
  let cleanName = (meta.name && meta.name !== feature) ? meta.name : feature;
  cleanName = cleanName.split(':').pop() || cleanName;
  cleanName = cleanName.replace(/HKQuantityTypeIdentifier|HKCategoryTypeIdentifier/gi, '')
             .replace(/_/g, ' ')
             .replace(/([A-Z])/g, ' $1')
             .replace(/^F /, '') 
             .trim()
             .replace(/\b\w/g, c => c.toUpperCase()); // e.g. "Active Energy"

  // 2. Direct Enumeration Lookup
  // If it's in our explicit list, return the final name with unit
  if (ENUMERATED_METRICS[cleanName]) return ENUMERATED_METRICS[cleanName];

  return cleanName;
};

// Adaptive time formatter based on data range
function makeTimeTickFormatter(timestamps) {
  const valid = (timestamps || []).filter(t => t && !isNaN(t))
  if (!valid.length) return () => ''
  // Plain loop: Math.min/max(...arr) throws RangeError on very large arrays.
  let minTs = Infinity
  let maxTs = -Infinity
  for (const ts of valid) {
    if (ts < minTs) minTs = ts
    if (ts > maxTs) maxTs = ts
  }
  const rangeMs = maxTs - minTs
  const rangeMin = rangeMs / 60000
  const d = (ts) => new Date(ts)
  if (rangeMin < 60) return (ts) => d(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  if (rangeMin < 60 * 24) return (ts) => d(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (rangeMin < 60 * 24 * 7) return (ts) => d(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  if (rangeMin < 60 * 24 * 31) return (ts) => d(ts).toLocaleDateString([], { month: 'short', day: 'numeric' })
  return (ts) => d(ts).toLocaleDateString([], { year: 'numeric', month: 'short' })
}

// formatFullDateTime imported from ../lib/utils

/**
 * MetricChartCard - A systematic, robust component to display metrics
 * Features:
 * - Dynamic aspect ratio (width > height)
 * - Maximized area usage (reduced padding/margins)
 * - Unified logic for Apple Health and GLOBEM
 * - Premium aesthetics (backdrop filters, optimized axis)
 */

/** Download a PNG blob (fallback when the async clipboard API is unavailable, e.g. plain http). */
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const MetricChartCard = memo(function MetricChartCard({
  feature, meta, featureData, isAppleHealthFormat, isMultiParticipant,
  aggregationMode, users, chartData,
}) {
  const { t } = useTranslation()
  const chart = useChartTheme()
  const [expanded, setExpanded] = useState(false)
  const expandedChartRef = useRef(null)
  const dialogRef = useRef(null)
  const [copyStatus, setCopyStatus] = useState('idle') // idle | copying | copied | downloaded | failed
  const statusTimerRef = useRef(null)

  // Display name / unit / value formatting derive from the feature metadata only.
  const { displayName, displayUnit, displayScale, formatDisplayValue } = useMemo(() => {
    const name = getFriendlyName(feature, meta)
    // Extract unit from "(unit)" pattern if it exists
    const nameMatches = name.match(/(.*?)\s*\((.*?)\)$/)
    const unit = nameMatches ? nameMatches[2] : ''
    const isPercentage = unit === '%'
    const formatStr = meta.format || '{:.2f}'
    const format = (chartValue) => {
      if (chartValue == null || typeof chartValue !== 'number' || isNaN(chartValue)) return 'N/A'
      let formattedStr = ''
      if (formatStr.includes('0f')) formattedStr = chartValue.toFixed(0)
      else if (formatStr.includes('1f')) formattedStr = chartValue.toFixed(1)
      else if (formatStr.includes('2f')) formattedStr = chartValue.toFixed(2)
      else formattedStr = String(chartValue)
      return isPercentage ? `${formattedStr}%` : formattedStr
    }
    return { displayName: name, displayUnit: unit, displayScale: meta.display_scale || 1, formatDisplayValue: format }
  }, [feature, meta])

  useEffect(() => () => {
    if (statusTimerRef.current) clearTimeout(statusTimerRef.current)
  }, [])

  const flashCopyStatus = useCallback((status) => {
    setCopyStatus(status)
    if (statusTimerRef.current) clearTimeout(statusTimerRef.current)
    statusTimerRef.current = setTimeout(() => {
      statusTimerRef.current = null
      setCopyStatus('idle')
    }, 2500)
  }, [])

  useEffect(() => {
    if (!expanded) return
    const handleEsc = (e) => { if (e.key === 'Escape') setExpanded(false) }
    document.addEventListener('keydown', handleEsc)
    document.body.style.overflow = 'hidden'
    // Move focus into the dialog so keyboard users land inside it.
    dialogRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', handleEsc)
      document.body.style.overflow = ''
    }
  }, [expanded])

  const handleCopyImage = useCallback(async () => {
    if (!expandedChartRef.current) return
    setCopyStatus('copying')
    try {
      const dataUrl = await toPng(expandedChartRef.current, { backgroundColor: chart.panel, pixelRatio: 2 })
      const res = await fetch(dataUrl)
      const blob = await res.blob()
      // navigator.clipboard / ClipboardItem only exist in secure contexts (https
      // or localhost); the dashboard is often served over plain http on a LAN.
      const canCopy = typeof window !== 'undefined' && window.isSecureContext
        && typeof ClipboardItem !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.write === 'function'
      if (canCopy) {
        try {
          await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
          flashCopyStatus('copied')
          return
        } catch (e) {
          console.warn('Clipboard write failed, downloading instead:', e)
        }
      }
      downloadBlob(blob, `${displayName.replace(/[^\w.-]+/g, '_') || 'chart'}.png`)
      flashCopyStatus('downloaded')
    } catch (e) {
      console.error('Copy failed:', e)
      flashCopyStatus('failed')
    }
  }, [displayName, flashCopyStatus, chart.panel])

  // Common chart preparation
  const timestamps = featureData.length > 0 ? featureData.map(d => d.timestamp).filter(Boolean) : []
  const timeFormatter = makeTimeTickFormatter(timestamps)
  const xKey = timestamps.length > 0 ? 'timestamp' : 'index'

  // Determine if it's the multi-line individual mode
  const isIndividualMulti = !isAppleHealthFormat && aggregationMode === 'individual' && isMultiParticipant
  // Apply display scaling from metadata (Single Source of Truth)
  const finalDisplayScale = displayScale || 1.0;
  const stats = useMemo(() => {
    const mainData = isIndividualMulti ? chartData : featureData.map(d => ({
      ...d,
      value: (d.value != null && typeof d.value === 'number' && !isNaN(d.value))
        ? d.value * finalDisplayScale
        : d.value
    }))
    const prefix = `${feature}__`

    // Calculate dynamic stats for labeling (single pass, no spread into Math.max/min)
    const validData = []
    const values = []
    let maxVal = -Infinity
    let minVal = Infinity
    for (const d of mainData) {
      if (isIndividualMulti) {
        let any = false
        for (const k of Object.keys(d)) {
          if (k.startsWith(prefix) && d[k] != null && !isNaN(d[k])) {
            any = true
            values.push(d[k])
          }
        }
        if (any) validData.push(d)
      } else if (d.value != null && !isNaN(d.value)) {
        validData.push(d)
        values.push(d.value)
      }
    }
    for (const v of values) {
      if (v > maxVal) maxVal = v
      if (v < minVal) minVal = v
    }
    if (values.length === 0) { maxVal = 0; minVal = 0 }

    // Find the max point for the callout
    const maxIdx = validData.findIndex(d => {
      if (isIndividualMulti) {
        return Object.keys(d).some(k => k.startsWith(prefix) && d[k] === maxVal)
      }
      return d.value === maxVal
    })
    return { mainData, validData, values, maxVal, minVal, maxIdx }
  }, [isIndividualMulti, chartData, featureData, finalDisplayScale, feature])
  const { mainData, validData, values, maxVal, minVal, maxIdx } = stats
  const isFlat = maxVal === minVal
  const maxPoint = maxIdx !== -1 ? validData[maxIdx] : null

  // Determine theme color for consistency
  let maxLinePidIdx = 0;
  if (isIndividualMulti && maxPoint) {
    const maxKey = Object.keys(maxPoint).find(k => k.startsWith(`${feature}__`) && maxPoint[k] === maxVal);
    if (maxKey) {
      const pid = maxKey.replace(`${feature}__`, '');
      maxLinePidIdx = users.indexOf(pid);
    }
  }
  // One fixed hue per health category (same metric, same colour); several
  // participants on one chart fall back to the distinct-hue series palette.
  const categoryColor = chart.colors[chartKeyFor(feature)]
  const seriesColor = (pidIdx) => chart.series[pidIdx % chart.series.length]
  const themeColor = isIndividualMulti ? seriesColor(maxLinePidIdx) : categoryColor

  return (
    <>
    <div
      role="button"
      tabIndex={0}
      aria-label={t('statistics.expand_chart', { name: displayName })}
      className="cursor-pointer bg-panel/80 backdrop-blur-3xl border border-panel/40 rounded-card p-5 shadow-sm hover:shadow-2xl  transition-all duration-700 flex flex-col h-full overflow-hidden group focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
      onClick={() => setExpanded(true)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          setExpanded(true)
        }
      }}
    >
      {/* Header Area */}
      <div className="flex-initial mb-1 flex items-start justify-between gap-2 px-1">
        <div className="min-w-0">
          <h5 className="text-base font-bold text-ink truncate leading-normal transition-all group-hover:text-info-ink tracking-tight" title={displayName}>
            {displayName}
          </h5>
          <div className="flex items-center gap-2 mt-0.5 opacity-60">
            <span className="text-[10px] font-bold uppercase tracking-widest text-ink-3">
              {isAppleHealthFormat ? `${featureData.length} ${t('statistics.points_suffix')}` : t('statistics.realtime')}
            </span>
          </div>
        </div>
      </div>

      {/* Extreme Area Fill Chart - Zero wasted space on sides */}
      <div className="flex-1 min-h-0 min-w-0 relative mt-2">
        {featureData.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center opacity-20">
              <div className="w-16 h-1 bg-ink mx-auto rounded-full mb-2" />
              <div className="font-bold uppercase tracking-[0.3em] text-[10px]">{t('statistics.syncing')}</div>
            </div>
          </div>
        ) : (
          <ResponsiveContainer width="100%" aspect={1.8}>
            <LineChart
              data={mainData}
              margin={{ top: 12, right: 10, left: 10, bottom: 0 }} // Minimal top gap
            >
              <CartesianGrid strokeDasharray="6 6" stroke={chart.grid} vertical={false} />
              <XAxis
                dataKey={xKey}
                type="number"
                domain={['dataMin', 'dataMax']}
                tickFormatter={timeFormatter}
                tick={{ fontSize: 9, fontWeight: 700, fill: chart.axisText }}
                axisLine={false}
                tickLine={false}
                minTickGap={60}
                height={25}
                padding={{ left: 20, right: 20 }} // Internal padding for labels without external margin waste
              />
              {/* Smart centering for single points, headroom for multi-points */}
              <YAxis 
                hide 
                domain={values.length === 1 
                  ? [values[0] - Math.abs(values[0] * 0.1) - 1, values[0] + Math.abs(values[0] * 0.1) + 1] 
                  : [minVal, maxVal + (isFlat ? (maxVal === 0 ? 1 : Math.abs(maxVal * 0.05)) : (maxVal - minVal) * 0.05)]
                } 
              />
              
              {/* Smart Max Point Callout - Always visible even for single points */}
              {maxPoint && (
                <ReferenceDot 
                  x={maxPoint[xKey]} 
                  y={maxVal} 
                  r={4}
                  fill={themeColor} 
                  stroke={chart.panel}
                  strokeWidth={2}
                  label={{ 
                    position: 'top',
                    textAnchor: validData.length <= 1 ? 'middle' : (maxIdx < 5 ? 'start' : (maxIdx > validData.length - 5 ? 'end' : 'middle')),
                    value: formatDisplayValue(maxVal), 
                    fill: themeColor, 
                    fontSize: 10, 
                    fontWeight: 900,
                    offset: 5,
                    style: { 
                      paintOrder: 'stroke',
                      stroke: chart.panel,
                      strokeWidth: '4px',
                      strokeLinejoin: 'round'
                    }
                  }}
                />
              )}

              <Tooltip
                contentStyle={{
                  ...chart.tooltip,
                  fontSize: 11,
                  fontWeight: 600,
                  borderRadius: '12px',
                  boxShadow: 'var(--shadow-2)',
                  padding: '10px 14px'
                }}
                labelStyle={{ color: chart.axisText }}
                labelFormatter={(val) => {
                  const pt = mainData.find(d => (d.timestamp ?? d.index) === val)
                  return pt?.date ? formatFullDateTime(pt.date) : String(val)
                }}
                formatter={(value, name) => [
                  <span key="val" className="text-info-ink font-bold">{formatDisplayValue(typeof value === 'number' ? value : undefined)}</span>,
                  <span key="lbl" className="text-ink-3 text-[10px] uppercase font-bold ml-1 tracking-tighter">{isIndividualMulti ? name : (displayUnit === '%' ? '' : (displayUnit || t('statistics.val_short')))}</span>
                ]}
              />
              {isIndividualMulti && (
                <Legend
                  verticalAlign="top"
                  align="right"
                  iconType="circle"
                  iconSize={6}
                  wrapperStyle={{
                    paddingTop: '0px',
                    paddingBottom: '10px',
                    fontSize: '9px',
                    fontWeight: 900,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em'
                  }}
                />
              )}

              {isIndividualMulti ? (
                users.map((pid, pidIdx) => pid && (
                  <Line
                    key={pid}
                    type="monotone"
                    dataKey={`${feature}__${pid}`}
                    stroke={seriesColor(pidIdx)}
                    strokeWidth={2.5}
                    dot={false}
                    activeDot={{ r: 4, strokeWidth: 0 }}
                    name={pid}
                    isAnimationActive={false}
                    connectNulls
                  />
                ))
              ) : (
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={categoryColor}
                  strokeWidth={3}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  isAnimationActive={false}
                  connectNulls
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>

    {/* Expanded Modal */}
    {expanded && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => setExpanded(false)}>
        <div
          ref={dialogRef}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-labelledby={`chart-modal-title-${feature}`}
          className="bg-panel rounded-card shadow-2xl max-w-3xl w-full mx-4 md:mx-8 outline-none"
          onClick={e => e.stopPropagation()}
        >
          {/* Modal Header */}
          <div className="flex items-center justify-between px-7 pt-5 pb-3">
            <div>
              <h3 id={`chart-modal-title-${feature}`} className="section-title">{displayName}</h3>
              {displayUnit && <p className="text-sm text-ink-3 font-medium mt-0.5">{displayUnit}</p>}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCopyImage}
                disabled={copyStatus === 'copying'}
                className={`flex items-center gap-2 px-4 py-2 rounded-control text-sm font-bold transition-all ${
                  copyStatus === 'copied' || copyStatus === 'downloaded'
                    ? 'bg-ok/10 text-ok-ink border border-ok/30'
                    : copyStatus === 'failed'
                      ? 'bg-bad/10 text-bad-ink border border-bad/30'
                      : 'bg-sunken text-ink-2 border border-line hover:bg-sunken'
                }`}
              >
                {copyStatus === 'copied' || copyStatus === 'downloaded' ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                <span role="status">
                  {copyStatus === 'copied' ? t('statistics.copied')
                    : copyStatus === 'downloaded' ? t('statistics.downloaded')
                    : copyStatus === 'failed' ? t('statistics.copy_failed')
                    : t('statistics.copy_image')}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setExpanded(false)}
                aria-label={t('common.close')}
                className="p-2 rounded-control text-ink-3 hover:text-ink-2 hover:bg-sunken transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Modal Chart */}
          <div ref={expandedChartRef} className="px-7 pt-2 pb-6 bg-panel rounded-b-3xl">
            <div className="text-base font-bold text-ink mb-1">{displayName}</div>
            {displayUnit && <div className="text-xs text-ink-3 mb-3">{displayUnit}</div>}
            {featureData.length === 0 ? (
              <div className="flex items-center justify-center h-64">
                <div className="text-center opacity-30">
                  <div className="font-bold uppercase tracking-[0.3em] text-sm">{t('statistics.no_data')}</div>
                </div>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={340}>
                <LineChart
                  data={mainData}
                  margin={{ top: 10, right: 20, left: 10, bottom: 20 }}
                >
                  <CartesianGrid strokeDasharray="4 4" stroke={chart.grid} />
                  <XAxis
                    dataKey={xKey}
                    type="number"
                    domain={['dataMin', 'dataMax']}
                    tickFormatter={timeFormatter}
                    tick={{ fontSize: 11, fontWeight: 600, fill: chart.axisText }}
                    axisLine={{ stroke: chart.axisLine }}
                    tickLine={{ stroke: chart.axisLine }}
                    minTickGap={60}
                    height={35}
                    padding={{ left: 15, right: 15 }}
                  />
                  <YAxis
                    domain={values.length === 1
                      ? [values[0] - Math.abs(values[0] * 0.1) - 1, values[0] + Math.abs(values[0] * 0.1) + 1]
                      : [minVal - (isFlat ? (maxVal === 0 ? 1 : Math.abs(maxVal * 0.1)) : (maxVal - minVal) * 0.05),
                         maxVal + (isFlat ? (maxVal === 0 ? 1 : Math.abs(maxVal * 0.1)) : (maxVal - minVal) * 0.05)]
                    }
                    tick={{ fontSize: 11, fontWeight: 600, fill: chart.axisText }}
                    axisLine={{ stroke: chart.axisLine }}
                    tickLine={{ stroke: chart.axisLine }}
                    width={60}
                    tickFormatter={(v) => formatDisplayValue(v)}
                  />

                  <Tooltip
                    contentStyle={{
                      ...chart.tooltip,
                      fontSize: 12, fontWeight: 600, borderRadius: '12px',
                      boxShadow: 'var(--shadow-2)', padding: '12px 16px'
                    }}
                    labelStyle={{ color: chart.axisText }}
                    labelFormatter={(val) => {
                      const pt = mainData.find(d => (d.timestamp ?? d.index) === val)
                      return pt?.date ? formatFullDateTime(pt.date) : String(val)
                    }}
                    formatter={(value, name) => [
                      <span key="val" className="text-info-ink font-bold">{formatDisplayValue(typeof value === 'number' ? value : undefined)}</span>,
                      <span key="lbl" className="text-ink-3 text-xs uppercase font-bold ml-1">{isIndividualMulti ? name : (displayUnit || t('statistics.value'))}</span>
                    ]}
                  />

                  {isIndividualMulti && (
                    <Legend verticalAlign="top" align="right" iconType="circle" iconSize={8}
                      wrapperStyle={{ fontSize: '11px', fontWeight: 700 }} />
                  )}

                  {isIndividualMulti ? (
                    users.map((pid, pidIdx) => pid && (
                      <Line key={pid} type="monotone" dataKey={`${feature}__${pid}`}
                        stroke={seriesColor(pidIdx)} strokeWidth={2.5}
                        dot={false} activeDot={{ r: 4, strokeWidth: 0 }}
                        name={pid} isAnimationActive={false} connectNulls />
                    ))
                  ) : (
                    <Line type="monotone" dataKey="value"
                      stroke={categoryColor} strokeWidth={2.5}
                      dot={false} activeDot={{ r: 4, strokeWidth: 0 }}
                      isAnimationActive={false} connectNulls />
                  )}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    )}
    </>
  )
})

// Taxonomy categories for the statistics panel

const TAXONOMY = [
  {
    id: 'heart',
    nameKey: 'statistics.category_heart',
    icon: Heart,
    chart: 'heart',
    color: 'text-chart-heart',
    bg: 'bg-chart-heart/10',
    matches: [
      'Heart', 'Pulse', 'Respiratory', 'Oxygen', 'Saturation', 'BloodPressure', 'Glucose',
      'Vitals', 'SpO2', 'Temperature', 'Beat', 'Atrial', 'Fibrillation', 'ECG', 'EKG',
      'Cardio', 'VO2'
    ]
  },
  {
    id: 'sleep',
    nameKey: 'statistics.category_sleep',
    icon: Moon,
    chart: 'sleep',
    color: 'text-chart-sleep',
    bg: 'bg-chart-sleep/10',
    matches: ['Sleep', 'Mindful', 'Rem', 'Arousal', 'Insomnia', 'Awake', 'DeepSleep']
  },
  {
    id: 'activity',
    nameKey: 'statistics.category_activity',
    icon: Activity,
    chart: 'activity',
    color: 'text-chart-activity',
    bg: 'bg-chart-activity/10',
    matches: [
      'Step', 'Distance', 'Flight', 'Energy', 'Calorie', 'Stand', 'Exercise', 'Move', 'Push',
      'Cycling', 'Swimming', 'Active', 'Downhill', 'Strokes', 'Cadence', 'Pace',
      'Velocity', 'Acceleration', 'Power', 'Metabolic'
    ]
  },
  {
    id: 'workouts',
    nameKey: 'statistics.category_workouts',
    icon: Dumbbell,
    chart: 'activity',
    color: 'text-chart-activity',
    bg: 'bg-chart-activity/10',
    matches: ['Workout']
  },
  {
    id: 'mobility',
    nameKey: 'statistics.category_mobility',
    icon: Footprints,
    chart: 'mind',
    color: 'text-chart-mind',
    bg: 'bg-chart-mind/10',
    matches: [
      'Gait', 'Walking', 'StepLength', 'Asymmetry', 'Steadiness', 'Balance', 'Stair',
      'SixMinute', 'Support', 'Swing', 'GroundContact', 'Vertical'
    ]
  },
  {
    id: 'environment',
    nameKey: 'statistics.category_environment',
    icon: Zap,
    chart: 'env',
    color: 'text-chart-env',
    bg: 'bg-chart-env/10',
    matches: [
      'Audio', 'Noise', 'Exposure', 'Dietary', 'Water', 'Nutrition', 'UV', 'Vitamin',
      'Sugar', 'Carb', 'Fat', 'Protein', 'Mineral', 'Micro', 'Milligram', 'Ounce', 'Fiber',
      'Iron', 'Calcium', 'Potassium', 'Sodium', 'Caffeine'
    ]
  },
  {
    id: 'body',
    nameKey: 'statistics.category_body',
    icon: Activity,
    chart: 'body',
    color: 'text-chart-body',
    bg: 'bg-chart-body/10',
    matches: [
      'Body', 'Mass', 'Fat', 'Height', 'Waist', 'BMI', 'Weight', 'Composition',
      'Menstrual', 'Period', 'Cycle', 'Ovulation', 'Symptoms', 'Sexual', 'Headache',
      'Mood', 'Fatigue', 'Sore', 'Pain', 'Health', 'General'
    ]
  }
];

/** Lower-case and strip everything but letters/digits so "Heart_Rate", "heart rate" and "HeartRate" compare equal. */
const normalizeFeatureName = (s) => String(s)
  .toLowerCase()
  .replace(/hkquantitytypeidentifier|hkcategorytypeidentifier|hkcharacteristictypeidentifier/g, '')
  .replace(/[^a-z0-9]/g, '')

// Keywords normalised once, not on every lookup.
const NORMALIZED_MATCHES = TAXONOMY.map(cat => ({ cat, keys: cat.matches.map(normalizeFeatureName) }))
const WORKOUT_CATEGORY = TAXONOMY.find(c => c.id === 'workouts')

export const getCategory = (feature) => {
  if (!feature) return TAXONOMY[TAXONOMY.length - 1];

  const f = normalizeFeatureName(feature);

  // Workout features also contain Activity keywords (Energy, Cycling, Distance…),
  // so the prefix has to win before the keyword scan.
  if (f.startsWith('workout')) return WORKOUT_CATEGORY;

  // Find category based on keyword matches (first TAXONOMY entry wins)
  for (const { cat, keys } of NORMALIZED_MATCHES) {
    if (keys.some(m => f.includes(m))) return cat;
  }

  // Default to General Wellness instead of "Uncategorized"
  return TAXONOMY[TAXONOMY.length - 1];
};

// Vital-sign metrics live in the Heart section but keep their own (vitals) hue.
const VITALS_KEYS = ['oxygen', 'saturation', 'spo2', 'respiratory', 'vo2', 'temperature', 'glucose', 'bloodpressure']

/** Chart colour key (see lib/chartColors.js) for a feature: fixed per metric. */
export const chartKeyFor = (feature) => {
  const f = normalizeFeatureName(feature || '')
  if (!f.includes('sleep') && VITALS_KEYS.some(k => f.includes(k))) return 'vitals'
  return getCategory(feature).chart || 'other'
}

const WINDOW_OPTIONS = [
  { labelKey: 'statistics.window_1hour', value: '1hour' },
  { labelKey: 'statistics.window_1day', value: '1day' },
  { labelKey: 'statistics.window_1week', value: '1week' },
  { labelKey: 'statistics.window_1month', value: '1month' },
]

const WINDOW_MS = {
  '1hour': 60 * 60 * 1000,
  '1day': 24 * 60 * 60 * 1000,
  '1week': 7 * 24 * 60 * 60 * 1000,
  '1month': 30 * 24 * 60 * 60 * 1000,
}

/**
 * Everything the panel derives from the stream buffer. Pure and heavy (the
 * buffer can hold tens of thousands of rows), so the component memoises it on
 * (data, historicalData, window, aggregation mode) instead of redoing it on
 * every render.
 */
export function computePanelData(data, historicalData, liveHistoryWindow, aggregationMode) {
  const safeHistorical = Array.isArray(historicalData) ? historicalData : []
  const windowSizeMs = WINDOW_MS[liveHistoryWindow] || WINDOW_MS['1hour']

  // Single pass over the history buffer: every record's timestamp is parsed
  // once, yielding the overall range, the sliding-window slice and that
  // slice's range.
  const parsedTs = new Array(safeHistorical.length)
  let totalMinTs = null
  let totalMaxTs = null
  for (let i = 0; i < safeHistorical.length; i++) {
    const r = safeHistorical[i]
    const ts = (r && r.date) ? parseBackendDate(r.date).getTime() : NaN
    const valid = !!ts && !isNaN(ts)
    parsedTs[i] = valid ? ts : null
    if (valid) {
      if (totalMinTs === null || ts < totalMinTs) totalMinTs = ts
      if (totalMaxTs === null || ts > totalMaxTs) totalMaxTs = ts
    }
  }
  const maxTs = totalMaxTs

  // Filter historical data to fit the sliding window
  let filteredHistorical = safeHistorical
  let visibleMinTs = totalMinTs
  let visibleMaxTs = totalMaxTs
  if (maxTs && windowSizeMs) {
    filteredHistorical = []
    visibleMinTs = null
    visibleMaxTs = null
    for (let i = 0; i < safeHistorical.length; i++) {
      const ts = parsedTs[i]
      if (ts && maxTs - ts <= windowSizeMs) {
        filteredHistorical.push(safeHistorical[i])
        if (visibleMinTs === null || ts < visibleMinTs) visibleMinTs = ts
        if (visibleMaxTs === null || ts > visibleMaxTs) visibleMaxTs = ts
      }
    }
  }

  const hasData = data && data.data && Array.isArray(data.data) && data.data.length > 0
  const dataToVisualize = hasData
    ? (filteredHistorical.length > 0 ? filteredHistorical : data.data)
    : []
  const hasDataToVisualize = Array.isArray(dataToVisualize) && dataToVisualize.length > 0

  // Check if multi-user
  const users = hasDataToVisualize
    ? [...new Set(dataToVisualize.map(r => r.pid))].filter(p => p)
    : []
  const isMultiParticipant = users.length > 1

  // Detect data format: always Apple Health for live data
  const sampleRecord = hasDataToVisualize ? (dataToVisualize[0] || {}) : {}
  const isAppleHealthFormat = 'feature_type' in sampleRecord

  // All features to display: derived directly from the data stream
  const featuresToDisplay = hasDataToVisualize
    ? [...new Set(dataToVisualize.map(r => r.feature_type))].filter(f => f)
    : []

  // Debug logging (development only)
  if (import.meta.env.DEV && featuresToDisplay.length > 0) {
    console.debug(`[StatisticsPanel] ${featuresToDisplay.length} features, ${dataToVisualize.length} records`)
  }

  // Prepare chart data based on aggregation mode
  let chartData = []

  try {
    if (isAppleHealthFormat) {
      // Apple Health: keep raw records with timestamps for time-aligned display
      const featureSet = new Set(featuresToDisplay)
      chartData = dataToVisualize
        .filter(record => record && featureSet.has(record.feature_type))
        .map((record, idx) => ({
          index: idx,
          date: record.date,
          timestamp: parseBackendDate(record.date).getTime(), // For sorting/grouping
          feature_type: record.feature_type,
          value: record.value,
          pid: record.pid
        }))

      // Sort by timestamp for proper time alignment
      chartData.sort((a, b) => a.timestamp - b.timestamp)
    } else if (aggregationMode === 'average' && isMultiParticipant) {
      // Group by date and calculate average
      const groupedByDate = {}
      dataToVisualize.forEach(record => {
        if (!record) return
        const dateKey = record.date || record.index || 'unknown'
        if (!groupedByDate[dateKey]) {
          groupedByDate[dateKey] = { sums: {}, counts: {} }
        }
        featuresToDisplay.forEach(feature => {
          if (!groupedByDate[dateKey].sums[feature]) {
            groupedByDate[dateKey].sums[feature] = 0
            groupedByDate[dateKey].counts[feature] = 0
          }
          const value = record[feature]
          if (value !== null && value !== undefined && !isNaN(value) && typeof value === 'number') {
            groupedByDate[dateKey].sums[feature] += value
            // Count per feature — records missing this feature must not
            // inflate its denominator.
            groupedByDate[dateKey].counts[feature]++
          }
        })
      })

      // Sort by date
      const sortedDates = Object.keys(groupedByDate).sort()
      chartData = sortedDates.map((dateKey, idx) => {
        const grouped = groupedByDate[dateKey]
        return {
          index: idx,
          date: dateKey,
          timestamp: dateKey ? parseBackendDate(dateKey).getTime() : null,
          ...Object.fromEntries(
            featuresToDisplay.map(feature => [
              feature,
              grouped.counts[feature] > 0 ? grouped.sums[feature] / grouped.counts[feature] : null
            ])
          )
        }
      })
    } else {
      // For individual mode, need unified timeline across all users
      // Group by date first to create aligned data points
      const allDates = [...new Set(dataToVisualize.map(r => r && r.date).filter(d => d))].sort()

      if (allDates.length > 0 && isMultiParticipant) {
        // One lookup table instead of a linear find per (date, user) pair.
        const byDatePid = new Map()
        for (const r of dataToVisualize) {
          if (r && r.date) byDatePid.set(`${r.date}\u0000${r.pid}`, r)
        }
        // Create a data point for each date, with values for each user
        chartData = allDates.map((date, idx) => {
          const point = {
            index: idx,
            date: date,
            timestamp: date ? parseBackendDate(date).getTime() : null,
          }

          // Add data for each user
          users.forEach(pid => {
            const pidData = byDatePid.get(`${date}\u0000${pid}`)
            featuresToDisplay.forEach(feature => {
              point[`${feature}__${pid}`] = pidData ? pidData[feature] : null
            })
          })

          return point
        })
      } else {
        // Single user - use simple index
        chartData = dataToVisualize
          .filter(r => r)
          .map((record, idx) => {
            const date = record.date || `Point ${idx + 1}`
            return {
              index: idx,
              date,
              timestamp: date && typeof date === 'string' ? parseBackendDate(date).getTime() : null,
              pid: record.pid,
              ...Object.fromEntries(
                featuresToDisplay.map((col) => [col, record[col]])
              ),
            }
          })
      }
    }
  } catch (error) {
    console.error('Error preparing chart data:', error)
    chartData = []
  }

  // Per-feature slices, grouped in one pass (not one full filter per feature).
  const featureDataMap = new Map()
  if (isAppleHealthFormat) {
    for (const record of chartData) {
      let list = featureDataMap.get(record.feature_type)
      if (!list) { list = []; featureDataMap.set(record.feature_type, list) }
      list.push(record)
    }
  }

  return {
    filteredHistorical, totalMinTs, totalMaxTs, visibleMinTs, visibleMaxTs,
    users, isMultiParticipant, isAppleHealthFormat, featuresToDisplay, chartData, featureDataMap,
  }
}

const EMPTY_META = {}
const EMPTY_DATA = []

/** Compact placeholder shown in place of a chart card that threw while rendering. */
function ChartCardError({ feature }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="bg-panel/80 border border-bad/30 rounded-card p-5 text-sm text-bad-ink">
      {t('statistics.chart_error', { name: getFriendlyName(feature) })}
    </div>
  )
}

function StatisticsPanel({ data, historicalData = [], featureMetadata = {}, liveHistoryWindow = '1hour', setLiveHistoryWindow }) {
  const { t } = useTranslation()
  const [aggregationMode, setAggregationMode] = useState('individual') // 'individual' or 'average'
  const [storageTotal, setStorageTotal] = useState(null)

  // Fetch true total storage count from the lightweight count endpoint.
  // (The dashboard endpoint truncates each feature to 2000 points for chart
  // rendering, so summing its arrays caps the total at ~features × 2000.)
  useEffect(() => {
    const ctrl = new AbortController()
    // Goes through lib/api so the optional auth header is attached.
    api.getDataCount(ctrl.signal).then(resp => {
      if (resp && resp.success && resp.count != null) {
        setStorageTotal(resp.count)
      }
    })
    return () => ctrl.abort()
  }, [])

  const {
    filteredHistorical, totalMinTs, totalMaxTs, visibleMinTs, visibleMaxTs,
    users, isMultiParticipant, isAppleHealthFormat, featuresToDisplay, chartData, featureDataMap,
  } = useMemo(
    () => computePanelData(data, historicalData, liveHistoryWindow, aggregationMode),
    [data, historicalData, liveHistoryWindow, aggregationMode],
  )

  // Features grouped by taxonomy category (cheap, but keyed so it only reruns with the feature set).
  const featuresByCategory = useMemo(() => {
    const map = new Map(TAXONOMY.map(c => [c.id, []]))
    for (const f of featuresToDisplay) map.get(getCategory(f).id).push(f)
    return map
  }, [featuresToDisplay])

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-3">
          <TrendingUp className="w-6 h-6 text-ink-2" />
          <h3 className="section-title">{t('statistics.data_overview')}</h3>
          {isMultiParticipant && (
            <span className="text-sm font-bold bg-info/15 text-info-ink px-3 py-1 rounded-control">
              {t('statistics.users_count', { count: users.length })}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          {isMultiParticipant && (
            <div className="flex items-center space-x-1 bg-sunken rounded-control p-1">
              <button
                onClick={() => setAggregationMode('individual')}
                className={`px-4 py-2 text-base font-semibold rounded-control ${aggregationMode === 'individual'
                  ? 'bg-panel text-ink shadow-md'
                  : 'text-ink-2 hover:text-ink'
                  }`}
                title={t('statistics.show_individual_tooltip')}
              >
                <Users className="w-5 h-5 inline mr-2" />
                {t('statistics.individual')}
              </button>
              <button
                onClick={() => setAggregationMode('average')}
                className={`px-4 py-2 text-base font-semibold rounded-control ${aggregationMode === 'average'
                  ? 'bg-panel text-ink shadow-md'
                  : 'text-ink-2 hover:text-ink'
                  }`}
                title={t('statistics.show_average_tooltip')}
              >
                <BarChart3 className="w-5 h-5 inline mr-2" />
                {t('statistics.average')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Top Stats Overview - unified 4-card row */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {/* Data Window Card */}
        <div className="bg-panel border border-line rounded-card p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-warn/10 rounded-control">
              <Calendar className="w-5 h-5 text-warn" />
            </div>
            <p className="text-sm font-bold text-ink-2 uppercase tracking-widest">{t('statistics.data_window')}</p>
          </div>
          <div className="grid grid-cols-2 gap-2 flex-1">
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setLiveHistoryWindow && setLiveHistoryWindow(opt.value)}
                aria-pressed={liveHistoryWindow === opt.value}
                className={`px-3 py-2.5 text-sm font-bold rounded-control transition-all ${
                  liveHistoryWindow === opt.value
                    ? 'bg-primary-600 text-white shadow-md transform scale-[1.02]'
                    : 'bg-sunken text-ink-2 border border-line hover:bg-sunken hover:border-line-2'
                }`}
              >
                {t(opt.labelKey)}
              </button>
            ))}
          </div>
          <div className="mt-3 text-xs text-ink-3 font-medium">{t('statistics.switch_window')}</div>
        </div>

        {/* Visible Data Card */}
        <div className="bg-panel border border-line rounded-card p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-info/10 rounded-control">
              <TrendingUp className="w-5 h-5 text-info" />
            </div>
            <p className="text-sm font-bold text-ink-2 uppercase tracking-widest">{t('statistics.visible_data')}</p>
          </div>
          <p className="text-4xl font-bold text-ink tabular-nums leading-none">{filteredHistorical.length.toLocaleString()}</p>
          <div className="mt-3 text-xs text-ink-3 font-medium">{t('statistics.records_in_window')}</div>
        </div>

        {/* Storage Total Card */}
        <div className="bg-panel border border-line rounded-card p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-ok/10 rounded-control">
              <Database className="w-5 h-5 text-ok" />
            </div>
            <p className="text-sm font-bold text-ink-2 uppercase tracking-widest">{t('statistics.storage_total')}</p>
          </div>
          <p className="text-4xl font-bold text-ink tabular-nums leading-none">{storageTotal !== null ? storageTotal.toLocaleString() : '—'}</p>
          <div className="mt-3 text-xs text-ink-3 font-medium">{t('statistics.storage_total_desc')}</div>
        </div>

        {/* Time Range Card */}
        <div className="bg-panel border border-line rounded-card p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-info/10 rounded-control">
              <Calendar className="w-5 h-5 text-info" />
            </div>
            <p className="text-sm font-bold text-ink-2 uppercase tracking-widest">{t('statistics.time_range')}</p>
          </div>

          <div className="space-y-3 flex-1">
            {/* Visible Window Range */}
            <div>
              <div className="text-[11px] font-bold text-info-ink uppercase tracking-tight mb-1.5 flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-info animate-pulse" />
                {t('statistics.current_window')}
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-ink-3 font-bold tracking-tighter">{t('statistics.start')}</span>
                  <span className="text-[11px] font-mono font-bold text-ink bg-sunken px-1.5 py-0.5 rounded-chip border border-line">
                    {visibleMinTs !== null ? formatFullDateTime(visibleMinTs) : '--'}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-ink-3 font-bold tracking-tighter">{t('statistics.end')}</span>
                  <span className="text-[11px] font-mono font-bold text-info-ink bg-info/10 px-1.5 py-0.5 rounded-chip border border-info/30">
                    {visibleMaxTs !== null ? formatFullDateTime(visibleMaxTs) : '--'}
                  </span>
                </div>
              </div>
            </div>

            {/* Total Storage Range */}
            <div className="pt-2 border-t border-dashed border-line">
              <div className="text-[11px] font-bold text-ink-2 uppercase tracking-tight mb-1.5 flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-line-2" />
                {t('statistics.total_history')}
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-ink-3 font-bold tracking-tighter">{t('statistics.start')}</span>
                  <span className="text-[11px] font-mono text-ink-2 italic font-medium px-1.5 py-0.5">
                    {totalMinTs !== null ? formatFullDateTime(totalMinTs) : '--'}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-ink-3 font-bold tracking-tighter">{t('statistics.end')}</span>
                  <span className="text-[11px] font-mono text-ink-2 italic font-medium px-1.5 py-0.5">
                    {totalMaxTs !== null ? formatFullDateTime(totalMaxTs) : '--'}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {chartData.length > 0 && featuresToDisplay.length > 0 && (
        <div className="space-y-12">
          {TAXONOMY.map(category => {
            const categoryFeatures = featuresByCategory.get(category.id) || [];
            if (categoryFeatures.length === 0) return null;

            return (
              <div key={category.id} className="space-y-6">
                {/* Category Header */}
                <div className="flex items-center gap-3 border-b border-line pb-4">
                  <div className={`p-2.5 ${category.bg} rounded-card shadow-sm`}>
                    <category.icon className={`w-6 h-6 ${category.color}`} />
                  </div>
                  <div>
                    <h4 className="text-xl font-bold text-ink tracking-tight">{t(category.nameKey)}</h4>
                    <p className="text-sm text-ink-3 font-medium">{t('statistics.metrics_identified', { count: categoryFeatures.length })}</p>
                  </div>
                </div>

                <div className={`${category.bg} p-6 rounded-card border border-line/50 shadow-inner`}>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {categoryFeatures.map((feature) => {
                      // For GLOBEM: skip if feature not present in chart data columns
                      if (!isAppleHealthFormat && chartData.length > 0 && !(feature in chartData[0])) return null

                      // For Apple Health: records pre-grouped by feature_type
                      const featureData = isAppleHealthFormat
                        ? (featureDataMap.get(feature) || EMPTY_DATA)
                        : (aggregationMode === 'individual' && isMultiParticipant
                          ? chartData  // Multi-user mode handled separately
                          : chartData.filter(point => point).map(point => ({
                            index: point.index,
                            date: point.date,
                            timestamp: point.timestamp,
                            pid: point.pid,
                            value: point[feature]
                          })))

                      if (!isAppleHealthFormat && featureData.length === 0) return null

                      // One failing chart must not blank the whole dashboard.
                      return (
                        <ErrorBoundary key={feature} fallback={<ChartCardError feature={feature} />}>
                          <MetricChartCard
                            feature={feature}
                            meta={featureMetadata[feature] || EMPTY_META}
                            featureData={featureData}
                            isAppleHealthFormat={isAppleHealthFormat}
                            isMultiParticipant={isMultiParticipant}
                            aggregationMode={aggregationMode}
                            users={users}
                            chartData={chartData}
                          />
                        </ErrorBoundary>
                      )
                    })}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

    </div>
  )
}

export default memo(StatisticsPanel)
