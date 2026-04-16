import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceDot } from 'recharts'
import { TrendingUp, Users, BarChart3, Database, Calendar, Activity, Heart, Moon, Zap, Footprints, Dumbbell, X, Copy, Check } from 'lucide-react'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { toPng } from 'html-to-image'
import { api } from '../lib/api'
import { formatFullDateTime, parseBackendDate } from '../lib/utils'

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
  if (!feature) return 'Unknown';

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
  const minTs = Math.min(...valid)
  const maxTs = Math.max(...valid)
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
const MetricChartCard = ({
  feature, displayName, displayUnit, displayScale,
  featureData, isAppleHealthFormat, isMultiParticipant,
  aggregationMode, users, chartData, colors,
  idx, formatDisplayValue, formatFullDateTime
}) => {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const expandedChartRef = useRef(null)
  const [copyStatus, setCopyStatus] = useState('idle')

  useEffect(() => {
    if (!expanded) return
    const handleEsc = (e) => { if (e.key === 'Escape') setExpanded(false) }
    document.addEventListener('keydown', handleEsc)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleEsc)
      document.body.style.overflow = ''
    }
  }, [expanded])

  const handleCopyImage = useCallback(async () => {
    if (!expandedChartRef.current) return
    setCopyStatus('copying')
    try {
      const dataUrl = await toPng(expandedChartRef.current, { backgroundColor: '#ffffff', pixelRatio: 2 })
      const res = await fetch(dataUrl)
      const blob = await res.blob()
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
      setCopyStatus('copied')
      setTimeout(() => setCopyStatus('idle'), 2000)
    } catch (e) {
      console.error('Copy failed:', e)
      setCopyStatus('idle')
    }
  }, [])

  // Common chart preparation
  const timestamps = featureData.length > 0 ? featureData.map(d => d.timestamp).filter(Boolean) : []
  const timeFormatter = makeTimeTickFormatter(timestamps)
  const xKey = timestamps.length > 0 ? 'timestamp' : 'index'

  // Determine if it's the multi-line individual mode
  const isIndividualMulti = !isAppleHealthFormat && aggregationMode === 'individual' && isMultiParticipant
  // Apply display scaling from metadata (Single Source of Truth)
  const finalDisplayScale = displayScale || 1.0;
  const mainData = isIndividualMulti ? chartData : featureData.map(d => ({
    ...d,
    value: (d.value != null && typeof d.value === 'number' && !isNaN(d.value))
      ? d.value * finalDisplayScale
      : d.value
  }))

  // Calculate dynamic stats for labeling
  const validData = mainData.filter(d => {
    if (isIndividualMulti) {
      return Object.keys(d).some(k => k.startsWith(`${feature}__`) && d[k] != null && !isNaN(d[k]))
    }
    return d.value != null && !isNaN(d.value)
  })

  const values = validData.flatMap(d => isIndividualMulti
    ? Object.keys(d).filter(k => k.startsWith(`${feature}__`)).map(k => d[k])
    : [d.value]
  ).filter(v => v != null && !isNaN(v))

  const hasEnoughPoints = validData.length > 2
  const maxVal = values.length > 0 ? Math.max(...values) : 0
  const minVal = values.length > 0 ? Math.min(...values) : 0
  const isFlat = maxVal === minVal
  const midVal = (maxVal + minVal) / 2

  // Find the max point for the callout
  const maxIdx = validData.findIndex(d => {
    if (isIndividualMulti) {
      return Object.keys(d).some(k => k.startsWith(`${feature}__`) && d[k] === maxVal)
    }
    return d.value === maxVal
  })
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
  const themeColor = isIndividualMulti ? colors[maxLinePidIdx % colors.length] : colors[idx % colors.length];
  
  // Custom darker color for the label text (simple darken logic)
  const getDarkColor = (hex) => {
    if (!hex || hex[0] !== '#') return hex;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgb(${Math.floor(r * 0.7)}, ${Math.floor(g * 0.7)}, ${Math.floor(b * 0.7)})`;
  };
  const darkThemeColor = getDarkColor(themeColor);

  return (
    <>
    <div
      className="cursor-pointer bg-white/80 backdrop-blur-3xl border border-white/40 rounded-[2.5rem] p-5 shadow-sm hover:shadow-2xl hover:shadow-blue-500/10 transition-all duration-700 flex flex-col h-full overflow-hidden group"
      onClick={() => setExpanded(true)}
    >
      {/* Header Area */}
      <div className="flex-initial mb-1 flex items-start justify-between gap-2 px-1">
        <div className="min-w-0">
          <h5 className="text-base font-black text-gray-900 truncate leading-normal transition-all group-hover:text-blue-600 tracking-tight" title={displayName}>
            {displayName}
          </h5>
          <div className="flex items-center gap-2 mt-0.5 opacity-60">
            <span className="text-[10px] font-black uppercase tracking-widest text-gray-400">
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
              <div className="w-16 h-1 bg-gray-900 mx-auto rounded-full mb-2" />
              <div className="font-black uppercase tracking-[0.3em] text-[10px]">{t('statistics.syncing')}</div>
            </div>
          </div>
        ) : (
          <ResponsiveContainer width="100%" aspect={1.8}>
            <LineChart
              data={mainData}
              margin={{ top: 12, right: 10, left: 10, bottom: 0 }} // Minimal top gap
            >
              <CartesianGrid strokeDasharray="6 6" stroke="#f1f5f9" vertical={false} />
              <XAxis
                dataKey={xKey}
                type="number"
                domain={['dataMin', 'dataMax']}
                tickFormatter={timeFormatter}
                tick={{ fontSize: 9, fontWeight: 900, fill: '#94a3b8' }}
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
                  stroke="#fff" 
                  strokeWidth={2}
                  label={{ 
                    position: 'top',
                    textAnchor: validData.length <= 1 ? 'middle' : (maxIdx < 5 ? 'start' : (maxIdx > validData.length - 5 ? 'end' : 'middle')),
                    value: formatDisplayValue(maxVal), 
                    fill: darkThemeColor, 
                    fontSize: 10, 
                    fontWeight: 900,
                    offset: 5,
                    style: { 
                      paintOrder: 'stroke',
                      stroke: '#ffffff',
                      strokeWidth: '4px',
                      strokeLinejoin: 'round'
                    }
                  }}
                />
              )}

              <Tooltip
                contentStyle={{
                  fontSize: 11,
                  fontWeight: 800,
                  borderRadius: '16px',
                  border: 'none',
                  boxShadow: '0 25px 50px -12px rgb(0 0 0 / 0.25)',
                  backgroundColor: 'rgba(255, 255, 255, 0.98)',
                  backdropFilter: 'blur(16px)',
                  padding: '10px 14px'
                }}
                labelFormatter={(val) => {
                  const pt = mainData.find(d => (d.timestamp ?? d.index) === val)
                  return pt?.date ? formatFullDateTime(pt.date) : String(val)
                }}
                formatter={(value, name) => [
                  <span key="val" className="text-blue-600 font-black">{formatDisplayValue(typeof value === 'number' ? value : undefined)}</span>,
                  <span key="lbl" className="text-gray-400 text-[10px] uppercase font-black ml-1 tracking-tighter">{isIndividualMulti ? name : (displayUnit === '%' ? '' : (displayUnit || 'Val'))}</span>
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
                    stroke={colors[pidIdx % colors.length]}
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
                  stroke={colors[idx % colors.length]}
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
        <div className="bg-white rounded-3xl shadow-2xl max-w-3xl w-full mx-8" onClick={e => e.stopPropagation()}>
          {/* Modal Header */}
          <div className="flex items-center justify-between px-7 pt-5 pb-3">
            <div>
              <h3 className="text-lg font-black text-gray-900">{displayName}</h3>
              {displayUnit && <p className="text-sm text-gray-400 font-medium mt-0.5">{displayUnit}</p>}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCopyImage}
                disabled={copyStatus === 'copying'}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold transition-all ${
                  copyStatus === 'copied'
                    ? 'bg-green-50 text-green-600 border border-green-200'
                    : 'bg-gray-50 text-gray-600 border border-gray-200 hover:bg-gray-100'
                }`}
              >
                {copyStatus === 'copied' ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                {copyStatus === 'copied' ? t('statistics.copied') : t('statistics.copy_image')}
              </button>
              <button
                onClick={() => setExpanded(false)}
                className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Modal Chart */}
          <div ref={expandedChartRef} className="px-7 pt-2 pb-6 bg-white rounded-b-3xl">
            <div className="text-base font-bold text-gray-800 mb-1">{displayName}</div>
            {displayUnit && <div className="text-xs text-gray-400 mb-3">{displayUnit}</div>}
            {featureData.length === 0 ? (
              <div className="flex items-center justify-center h-64">
                <div className="text-center opacity-30">
                  <div className="font-black uppercase tracking-[0.3em] text-sm">{t('statistics.no_data')}</div>
                </div>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={340}>
                <LineChart
                  data={mainData}
                  margin={{ top: 10, right: 20, left: 10, bottom: 20 }}
                >
                  <CartesianGrid strokeDasharray="4 4" stroke="#e2e8f0" />
                  <XAxis
                    dataKey={xKey}
                    type="number"
                    domain={['dataMin', 'dataMax']}
                    tickFormatter={timeFormatter}
                    tick={{ fontSize: 11, fontWeight: 600, fill: '#64748b' }}
                    axisLine={{ stroke: '#cbd5e1' }}
                    tickLine={{ stroke: '#cbd5e1' }}
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
                    tick={{ fontSize: 11, fontWeight: 600, fill: '#64748b' }}
                    axisLine={{ stroke: '#cbd5e1' }}
                    tickLine={{ stroke: '#cbd5e1' }}
                    width={60}
                    tickFormatter={(v) => formatDisplayValue(v)}
                  />

                  <Tooltip
                    contentStyle={{
                      fontSize: 12, fontWeight: 700, borderRadius: '12px', border: 'none',
                      boxShadow: '0 10px 40px -10px rgb(0 0 0 / 0.2)',
                      backgroundColor: 'rgba(255, 255, 255, 0.98)', padding: '12px 16px'
                    }}
                    labelFormatter={(val) => {
                      const pt = mainData.find(d => (d.timestamp ?? d.index) === val)
                      return pt?.date ? formatFullDateTime(pt.date) : String(val)
                    }}
                    formatter={(value, name) => [
                      <span key="val" className="text-blue-600 font-black">{formatDisplayValue(typeof value === 'number' ? value : undefined)}</span>,
                      <span key="lbl" className="text-gray-400 text-xs uppercase font-bold ml-1">{isIndividualMulti ? name : (displayUnit || 'Value')}</span>
                    ]}
                  />

                  {isIndividualMulti && (
                    <Legend verticalAlign="top" align="right" iconType="circle" iconSize={8}
                      wrapperStyle={{ fontSize: '11px', fontWeight: 700 }} />
                  )}

                  {isIndividualMulti ? (
                    users.map((pid, pidIdx) => pid && (
                      <Line key={pid} type="monotone" dataKey={`${feature}__${pid}`}
                        stroke={colors[pidIdx % colors.length]} strokeWidth={2.5}
                        dot={false} activeDot={{ r: 4, strokeWidth: 0 }}
                        name={pid} isAnimationActive={false} connectNulls />
                    ))
                  ) : (
                    <Line type="monotone" dataKey="value"
                      stroke={colors[idx % colors.length]} strokeWidth={2.5}
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
}

// Taxonomy categories for the statistics panel

const TAXONOMY = [
  {
    id: 'heart',
    nameKey: 'statistics.category_heart',
    icon: Heart,
    color: 'text-red-500',
    bg: 'bg-red-50',
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
    color: 'text-indigo-500',
    bg: 'bg-indigo-50',
    matches: ['Sleep', 'Mindful', 'Rem', 'Arousal', 'Insomnia', 'Awake', 'DeepSleep']
  },
  {
    id: 'activity',
    nameKey: 'statistics.category_activity',
    icon: Activity,
    color: 'text-orange-500',
    bg: 'bg-orange-50',
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
    color: 'text-amber-600',
    bg: 'bg-amber-50',
    matches: [
      'Workout Running', 'Workout Cycling', 'Workout Swimming', 'Workout Walking',
      'Workout Hiking', 'Workout Yoga', 'Workout Strength', 'Workout Hiit',
      'Workout Elliptical', 'Workout Rowing', 'Workout Core', 'Workout Flexibility',
      'Workout Cooldown', 'workout_running', 'workout_cycling', 'workout_swimming',
      'workout_walking', 'workout_hiking', 'workout_yoga', 'workout_strength',
      'workout_hiit', 'workout_elliptical', 'workout_rowing', 'workout_core',
      'workout_flexibility', 'workout_cooldown'
    ]
  },
  {
    id: 'mobility',
    nameKey: 'statistics.category_mobility',
    icon: Footprints,
    color: 'text-emerald-500',
    bg: 'bg-emerald-50',
    matches: [
      'Gait', 'Walking', 'StepLength', 'Asymmetry', 'Steadiness', 'Balance', 'Stair',
      'SixMinute', 'Support', 'Swing', 'GroundContact', 'Vertical'
    ]
  },
  {
    id: 'environment',
    nameKey: 'statistics.category_environment',
    icon: Zap,
    color: 'text-amber-500',
    bg: 'bg-amber-50',
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
    color: 'text-cyan-500',
    bg: 'bg-cyan-50',
    matches: [
      'Body', 'Mass', 'Fat', 'Height', 'Waist', 'BMI', 'Weight', 'Composition',
      'Menstrual', 'Period', 'Cycle', 'Ovulation', 'Symptoms', 'Sexual', 'Headache',
      'Mood', 'Fatigue', 'Sore', 'Pain', 'Health', 'General'
    ]
  }
];

const getCategory = (feature) => {
  if (!feature) return TAXONOMY[TAXONOMY.length - 1];

  const f = feature.toLowerCase();

  // Clean up common prefixes to improve matching
  const cleanF = f.replace(/hkquantitytypeidentifier|hkcategorytypeidentifier|hkcharacteristictypeidentifier/gi, '');

  // Find category based on keyword matches
  // Iterate through TAXONOMY to find first match
  for (const cat of TAXONOMY) {
    if (cat.matches.some(m => cleanF.includes(m.toLowerCase()) || f.includes(m.toLowerCase()))) {
      return cat;
    }
  }

  // Default to General Wellness instead of "Uncategorized"
  return TAXONOMY[TAXONOMY.length - 1];
};

const WINDOW_OPTIONS = [
  { labelKey: 'statistics.window_1hour', value: '1hour' },
  { labelKey: 'statistics.window_1day', value: '1day' },
  { labelKey: 'statistics.window_1week', value: '1week' },
  { labelKey: 'statistics.window_1month', value: '1month' },
]

export default function StatisticsPanel({ data, historicalData = [], featureMetadata = {}, liveHistoryWindow = '1hour', setLiveHistoryWindow, isStreaming = false }) {
  const { t } = useTranslation()
  const [aggregationMode, setAggregationMode] = useState('individual') // 'individual' or 'average'
  const [storageTotal, setStorageTotal] = useState(null)

  // Fetch true total storage count from the lightweight count endpoint.
  // (The dashboard endpoint truncates each feature to 2000 points for chart
  // rendering, so summing its arrays caps the total at ~features × 2000.)
  useEffect(() => {
    const ctrl = new AbortController()
    fetch('/api/data/count', { signal: ctrl.signal })
      .then(r => r.json())
      .then(resp => {
        if (resp && resp.success && resp.count != null) {
          setStorageTotal(resp.count)
        }
      })
      .catch(err => {
        if (err.name !== 'AbortError') console.error(err)
      })
    return () => ctrl.abort()
  }, [])

  // Derive data-dependent variables unconditionally (before any early returns)
  const safeHistorical = Array.isArray(historicalData) ? historicalData : []

  // Calculate sliding window
  const windowMsMap = {
    '1hour': 60 * 60 * 1000,
    '1day': 24 * 60 * 60 * 1000,
    '1week': 7 * 24 * 60 * 60 * 1000,
    '1month': 30 * 24 * 60 * 60 * 1000,
  }
  const windowSizeMs = windowMsMap[liveHistoryWindow] || windowMsMap['1hour']

  // Find max timestamp in historical data
  const validTimestamps = safeHistorical
    .map(r => (r && r.date) ? parseBackendDate(r.date).getTime() : null)
    .filter(t => t && !isNaN(t))
  const maxTs = validTimestamps.length > 0 ? Math.max(...validTimestamps) : null

  // Filter historical data to fit the sliding window
  const filteredHistorical = (maxTs && windowSizeMs)
    ? safeHistorical.filter(r => {
      const ts = (r && r.date) ? parseBackendDate(r.date).getTime() : null
      return ts && (maxTs - ts <= windowSizeMs)
    })
    : safeHistorical

  // Calculate timestamps for both total and filtered (visible) data
  const visibleTimestamps = filteredHistorical
    .map(r => (r && r.date) ? parseBackendDate(r.date).getTime() : null)
    .filter(t => t && !isNaN(t))

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
      chartData = dataToVisualize
        .filter(record => record && featuresToDisplay.includes(record.feature_type))
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
          groupedByDate[dateKey] = { count: 0, sums: {} }
        }
        groupedByDate[dateKey].count++
        featuresToDisplay.forEach(feature => {
          if (!groupedByDate[dateKey].sums[feature]) {
            groupedByDate[dateKey].sums[feature] = 0
          }
          const value = record[feature]
          if (value !== null && value !== undefined && !isNaN(value) && typeof value === 'number') {
            groupedByDate[dateKey].sums[feature] += value
          }
        })
      })

      // Sort by date
      const sortedDates = Object.keys(groupedByDate).sort()
      chartData = sortedDates.map((dateKey, idx) => {
        const data = groupedByDate[dateKey]
        return {
          index: idx,
          date: dateKey,
          timestamp: dateKey ? new Date(dateKey).getTime() : null,
          ...Object.fromEntries(
            featuresToDisplay.map(feature => [
              feature,
              data.count > 0 ? data.sums[feature] / data.count : null
            ])
          )
        }
      })
    } else {
      // For individual mode, need unified timeline across all users
      // Group by date first to create aligned data points
      const allDates = [...new Set(dataToVisualize.map(r => r && r.date).filter(d => d))].sort()

      if (allDates.length > 0 && isMultiParticipant) {
        // Create a data point for each date, with values for each user
        chartData = allDates.map((date, idx) => {
          const point = {
            index: idx,
            date: date,
            timestamp: date ? new Date(date).getTime() : null,
          }

          // Add data for each user
          users.forEach(pid => {
            const pidData = dataToVisualize.find(r => r && r.date === date && r.pid === pid)
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
              timestamp: date && typeof date === 'string' ? new Date(date).getTime() : null,
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

  const colors = ['#0ea5e9', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444', '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16', '#e11d48', '#0d9488']

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-3">
          <TrendingUp className="w-6 h-6 text-gray-600" />
          <h3 className="text-lg font-extrabold text-gray-900">{t('statistics.data_overview')}</h3>
          {isMultiParticipant && (
            <span className="text-sm font-bold bg-blue-100 text-blue-700 px-3 py-1 rounded-lg">
              {t('statistics.users_count', { count: users.length })}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          {isMultiParticipant && (
            <div className="flex items-center space-x-1 bg-gray-100 rounded-lg p-1">
              <button
                onClick={() => setAggregationMode('individual')}
                className={`px-4 py-2 text-base font-semibold rounded-lg ${aggregationMode === 'individual'
                  ? 'bg-white text-gray-900 shadow-md'
                  : 'text-gray-600 hover:text-gray-900'
                  }`}
                title={t('statistics.show_individual_tooltip')}
              >
                <Users className="w-5 h-5 inline mr-2" />
                {t('statistics.individual')}
              </button>
              <button
                onClick={() => setAggregationMode('average')}
                className={`px-4 py-2 text-base font-semibold rounded-lg ${aggregationMode === 'average'
                  ? 'bg-white text-gray-900 shadow-md'
                  : 'text-gray-600 hover:text-gray-900'
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
        <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-orange-50 rounded-lg">
              <Calendar className="w-5 h-5 text-orange-500" />
            </div>
            <p className="text-sm font-bold text-gray-500 uppercase tracking-widest">{t('statistics.data_window')}</p>
          </div>
          <div className="grid grid-cols-2 gap-2 flex-1">
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setLiveHistoryWindow && setLiveHistoryWindow(opt.value)}
                className={`px-3 py-2.5 text-sm font-bold rounded-lg transition-all ${
                  liveHistoryWindow === opt.value
                    ? 'bg-primary-600 text-white shadow-md transform scale-[1.02]'
                    : 'bg-gray-50 text-gray-600 border border-gray-200 hover:bg-gray-100 hover:border-gray-300'
                }`}
              >
                {t(opt.labelKey)}
              </button>
            ))}
          </div>
          <div className="mt-3 text-xs text-gray-400 font-medium">{t('statistics.switch_window')}</div>
        </div>

        {/* Visible Data Card */}
        <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-blue-50 rounded-lg">
              <TrendingUp className="w-5 h-5 text-blue-500" />
            </div>
            <p className="text-sm font-bold text-gray-500 uppercase tracking-widest">{t('statistics.visible_data')}</p>
          </div>
          <p className="text-4xl font-extrabold text-gray-900 tabular-nums leading-none">{filteredHistorical.length.toLocaleString()}</p>
          <div className="mt-3 text-xs text-gray-400 font-medium">{t('statistics.records_in_window')}</div>
        </div>

        {/* Storage Total Card */}
        <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-green-50 rounded-lg">
              <Database className="w-5 h-5 text-green-500" />
            </div>
            <p className="text-sm font-bold text-gray-500 uppercase tracking-widest">{t('statistics.storage_total')}</p>
          </div>
          <p className="text-4xl font-extrabold text-gray-900 tabular-nums leading-none">{storageTotal !== null ? storageTotal.toLocaleString() : '—'}</p>
          <div className="mt-3 text-xs text-gray-400 font-medium">{t('statistics.storage_total_desc')}</div>
        </div>

        {/* Time Range Card */}
        <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <div className="p-2 bg-purple-50 rounded-lg">
              <Calendar className="w-5 h-5 text-purple-500" />
            </div>
            <p className="text-sm font-bold text-gray-500 uppercase tracking-widest">{t('statistics.time_range')}</p>
          </div>

          <div className="space-y-3 flex-1">
            {/* Visible Window Range */}
            <div>
              <div className="text-[11px] font-bold text-blue-600 uppercase tracking-tight mb-1.5 flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                {t('statistics.current_window')}
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-gray-400 font-bold tracking-tighter">{t('statistics.start')}</span>
                  <span className="text-[11px] font-mono font-bold text-gray-700 bg-gray-50 px-1.5 py-0.5 rounded-md border border-gray-100">
                    {visibleTimestamps.length > 0 ? formatFullDateTime(Math.min(...visibleTimestamps)) : '--'}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-gray-400 font-bold tracking-tighter">{t('statistics.end')}</span>
                  <span className="text-[11px] font-mono font-bold text-blue-700 bg-blue-50 px-1.5 py-0.5 rounded-md border border-blue-100">
                    {visibleTimestamps.length > 0 ? formatFullDateTime(Math.max(...visibleTimestamps)) : '--'}
                  </span>
                </div>
              </div>
            </div>

            {/* Total Storage Range */}
            <div className="pt-2 border-t border-dashed border-gray-100">
              <div className="text-[11px] font-bold text-gray-500 uppercase tracking-tight mb-1.5 flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-gray-300" />
                {t('statistics.total_history')}
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-gray-400 font-bold tracking-tighter">{t('statistics.start')}</span>
                  <span className="text-[11px] font-mono text-gray-500 italic font-medium px-1.5 py-0.5">
                    {validTimestamps.length > 0 ? formatFullDateTime(Math.min(...validTimestamps)) : '--'}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-gray-400 font-bold tracking-tighter">{t('statistics.end')}</span>
                  <span className="text-[11px] font-mono text-gray-500 italic font-medium px-1.5 py-0.5">
                    {validTimestamps.length > 0 ? formatFullDateTime(Math.max(...validTimestamps)) : '--'}
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
            const categoryFeatures = featuresToDisplay.filter(f => getCategory(f).id === category.id);
            if (categoryFeatures.length === 0) return null;

            return (
              <div key={category.id} className="space-y-6">
                {/* Category Header */}
                <div className="flex items-center gap-3 border-b border-gray-100 pb-4">
                  <div className={`p-2.5 ${category.bg} rounded-xl shadow-sm`}>
                    <category.icon className={`w-6 h-6 ${category.color}`} />
                  </div>
                  <div>
                    <h4 className="text-xl font-black text-gray-900 tracking-tight">{t(category.nameKey)}</h4>
                    <p className="text-sm text-gray-400 font-medium">{t('statistics.metrics_identified', { count: categoryFeatures.length })}</p>
                  </div>
                </div>

                <div className={`${category.bg} p-6 rounded-[2.5rem] border border-gray-100/50 shadow-inner-sm`}>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {categoryFeatures.map((feature) => {
                      const idx = featuresToDisplay.indexOf(feature);

                      // For GLOBEM: skip if feature not present in chart data columns
                      if (!isAppleHealthFormat && chartData.length > 0 && !(feature in chartData[0])) return null

                      // For Apple Health: filter records by feature_type
                      const featureData = isAppleHealthFormat
                        ? chartData.filter(record => record.feature_type === feature)
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

                      // Display name and indicator parsing
                      const meta = featureMetadata[feature] || {}
                      const displayName = getFriendlyName(feature, meta)
                      
                      // Extract unit from "(unit)" pattern if it exists
                      const nameMatches = displayName.match(/(.*?)\s*\((.*?)\)$/);
                      const displayUnit = nameMatches ? nameMatches[2] : '';
                      const isPercentage = displayUnit === '%';

                      const displayScale = meta.display_scale || 1
                      const formatStr = meta.format || '{:.2f}'

                      const formatDisplayValue = (chartValue) => {
                        if (chartValue == null || typeof chartValue !== 'number' || isNaN(chartValue)) return 'N/A'
                        let formattedStr = '';
                        if (formatStr.includes('0f')) formattedStr = chartValue.toFixed(0)
                        else if (formatStr.includes('1f')) formattedStr = chartValue.toFixed(1)
                        else if (formatStr.includes('2f')) formattedStr = chartValue.toFixed(2)
                        else formattedStr = String(chartValue)
                        
                        return isPercentage ? `${formattedStr}%` : formattedStr;
                      }

                      // Use a unified, robust chart component for both formats
                      return (
                        <MetricChartCard
                          key={feature}
                          feature={feature}
                          displayName={displayName}
                          displayUnit={displayUnit}
                          displayScale={displayScale}
                          featureData={featureData}
                          isAppleHealthFormat={isAppleHealthFormat}
                          isMultiParticipant={isMultiParticipant}
                          aggregationMode={aggregationMode}
                          users={users}
                          chartData={chartData}
                          colors={colors}
                          idx={idx}
                          formatDisplayValue={formatDisplayValue}
                          formatFullDateTime={formatFullDateTime}
                        />
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
