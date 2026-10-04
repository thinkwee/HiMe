/**
 * Chart colours. Every health category owns one fixed hue (CSS tokens --c-*,
 * defined with light + dark variants in index.css), so a metric always draws in
 * the same colour. Recharts/SVG need concrete colour strings (the PNG export also
 * serialises the SVG outside the document), so tokens are resolved here rather
 * than passed as var(...).
 */
import { useMemo } from 'react'
import { useTheme } from './theme'

export const CHART_KEYS = ['heart', 'sleep', 'activity', 'body', 'vitals', 'mind', 'env', 'other']

const FALLBACK = {
  light: {
    heart: '224 108 120', sleep: '112 120 186', activity: '226 148 40', body: '90 173 80',
    vitals: '76 164 214', mind: '62 160 152', env: '168 140 112', other: '150 140 130',
    panel: '255 255 255', ink: '36 31 26', ink2: '107 97 87', ink3: '152 141 129',
    line: '234 227 218', line2: '216 206 193',
  },
  dark: {
    heart: '240 130 142', sleep: '142 150 214', activity: '242 172 70', body: '118 196 108',
    vitals: '110 190 232', mind: '88 196 186', env: '196 168 138', other: '168 158 148',
    panel: '36 31 26', ink: '245 237 225', ink2: '196 184 169', ink3: '143 131 118',
    line: '62 54 46', line2: '86 76 66',
  },
}

const VAR_NAMES = {
  heart: '--c-heart', sleep: '--c-sleep', activity: '--c-activity', body: '--c-body',
  vitals: '--c-vitals', mind: '--c-mind', env: '--c-env', other: '--c-other',
  panel: '--panel', ink: '--ink', ink2: '--ink-2', ink3: '--ink-3', line: '--line', line2: '--line-2',
}

function readVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return /^\d+\s+\d+\s+\d+$/.test(v) ? v : fallback
  } catch {
    return fallback
  }
}

/** Resolve the chart-relevant tokens to concrete `rgb(...)` strings for the given theme. */
export function readChartTheme(resolved = 'light') {
  const fb = FALLBACK[resolved] || FALLBACK.light
  const out = {}
  for (const [k, cssVar] of Object.entries(VAR_NAMES)) {
    out[k] = `rgb(${readVar(cssVar, fb[k]).replace(/\s+/g, ' ')})`
  }
  const colors = Object.fromEntries(CHART_KEYS.map((k) => [k, out[k]]))
  return {
    colors,
    // Distinct hues for multi-series (several participants) charts.
    series: ['heart', 'vitals', 'activity', 'sleep', 'body', 'mind', 'env', 'other'].map((k) => out[k]),
    panel: out.panel,
    text: out.ink,
    axisText: out.ink3,
    grid: out.line,
    axisLine: out.line2,
    tooltip: {
      backgroundColor: out.panel,
      color: out.ink,
      border: `1px solid ${out.line2}`,
    },
  }
}

/** Themed chart palette; recomputed whenever the effective theme changes. */
export function useChartTheme() {
  const { resolved } = useTheme()
  return useMemo(() => readChartTheme(resolved), [resolved])
}
