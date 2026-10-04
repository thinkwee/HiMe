/**
 * i18n guard rails: every literal translation key used in the source must exist
 * in BOTH locales, and the two locale files must stay in sync (same keys, same
 * {{interpolation}} variables).
 */
import en from '../i18n/locales/en.json'
import zh from '../i18n/locales/zh.json'

// All app source (not tests / mocks), loaded as raw text.
const sources = import.meta.glob(['../**/*.js', '../**/*.jsx', '!../__tests__/**', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

function flatten(obj, prefix = '', out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, key, out)
    else out[key] = v
  }
  return out
}

const enFlat = flatten(en)
const zhFlat = flatten(zh)

// t('a.b'), i18n.t('a.b'), tr('a.b') (agentEvents), and *Key: 'a.b' / i18nKey="a.b" config fields.
const CALL_RE = /(?:^|[^\w.$])(?:i18n\.)?(?:t|tr)\(\s*(['"])([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\1/g
const KEY_FIELD_RE = /\b(?:labelKey|nameKey|titleKey|i18nKey)\s*[:=]\s*(['"])([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\1/g

function collectKeys() {
  const found = new Map() // key -> first file
  for (const [file, text] of Object.entries(sources)) {
    for (const re of [CALL_RE, KEY_FIELD_RE]) {
      re.lastIndex = 0
      let m
      while ((m = re.exec(text)) !== null) {
        if (!found.has(m[2])) found.set(m[2], file)
      }
    }
  }
  return found
}

describe('i18n keys', () => {
  it('finds a meaningful number of literal keys (guards the scanner itself)', () => {
    expect(collectKeys().size).toBeGreaterThan(100)
  })

  it('every literal key used in src exists in en.json and zh.json', () => {
    const missing = []
    for (const [key, file] of collectKeys()) {
      if (!(key in enFlat)) missing.push(`en: ${key} (${file})`)
      if (!(key in zhFlat)) missing.push(`zh: ${key} (${file})`)
    }
    expect(missing).toEqual([])
  })

  it('en.json and zh.json have identical key sets', () => {
    const onlyEn = Object.keys(enFlat).filter((k) => !(k in zhFlat))
    const onlyZh = Object.keys(zhFlat).filter((k) => !(k in enFlat))
    expect({ onlyEn, onlyZh }).toEqual({ onlyEn: [], onlyZh: [] })
  })

  it('translations use the same {{variables}} in both locales', () => {
    const vars = (s) => (String(s).match(/\{\{\s*\w+\s*\}\}/g) || []).map((v) => v.replace(/\s/g, '')).sort().join(',')
    const mismatched = Object.keys(enFlat)
      .filter((k) => k in zhFlat && vars(enFlat[k]) !== vars(zhFlat[k]))
    expect(mismatched).toEqual([])
  })

  it('has no empty translations', () => {
    const empty = [...Object.entries(enFlat), ...Object.entries(zhFlat)].filter(([, v]) => v === '').map(([k]) => k)
    expect(empty).toEqual([])
  })
})
