/**
 * Small display blocks shared by the monitor's timeline and its raw log:
 * syntax-highlighted code, compact SQL result table, authed chart image.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Light as SyntaxHighlighter } from 'react-syntax-highlighter'
import python from 'react-syntax-highlighter/dist/esm/languages/hljs/python'
import sql from 'react-syntax-highlighter/dist/esm/languages/hljs/sql'
import { githubGist, atomOneDark } from 'react-syntax-highlighter/dist/esm/styles/hljs'
import { api } from '../../lib/api'
import { useTheme } from '../../lib/theme'

SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('sql', sql)

const codeHighlightStyles = {
  light: { ...githubGist, hljs: { ...githubGist.hljs, background: 'transparent', padding: 0 } },
  dark: { ...atomOneDark, hljs: { ...atomOneDark.hljs, background: 'transparent', padding: 0 } },
}

/** Syntax-highlighted code in a capped, scrollable block (theme-aware). */
export function CodeBlock({ code, language = 'python', className = '', maxH = 'max-h-40', fontSize = '11px' }) {
  const { resolved } = useTheme()
  const style = codeHighlightStyles[resolved] || codeHighlightStyles.light
  return (
    <div className={`rounded-control bg-sunken border border-line px-3 py-2 ${maxH} overflow-auto ${className}`}>
      <SyntaxHighlighter language={language} style={style} customStyle={{ fontSize, margin: 0, background: 'transparent' }}>
        {code}
      </SyntaxHighlighter>
    </div>
  )
}

/** Plain monospace output block, capped. */
export function OutputBlock({ text, className = '', maxH = 'max-h-48' }) {
  return (
    <pre className={`rounded-control bg-sunken border border-line px-3 py-2 ${maxH} overflow-auto font-mono text-[11px] leading-relaxed text-ink-2 whitespace-pre-wrap break-words ${className}`}>
      {text}
    </pre>
  )
}

/** Compact SQL result table (first `maxRows` rows). */
export function SqlTable({ columns, rows, maxRows = 50, className = '' }) {
  const { t } = useTranslation()
  const shown = rows.slice(0, maxRows)
  return (
    <div className={`overflow-auto max-h-60 rounded-control border border-line ${className}`}>
      <table className="w-full border-collapse text-[11px] tabular-nums">
        <thead className="sticky top-0 bg-sunken text-ink-2">
          <tr>
            {columns.map((col, i) => (
              <th key={i} scope="col" className="px-2 py-1 text-left font-semibold border-b border-line whitespace-nowrap">{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, ri) => (
            <tr key={ri} className={ri % 2 ? 'bg-sunken/50' : ''}>
              {row.map((val, ci) => {
                const str = val == null ? '' : String(val)
                return (
                  <td key={ci} className="px-2 py-0.5 text-ink max-w-[16rem] truncate font-mono" title={str.length > 40 ? str : undefined}>
                    {val == null ? <span className="text-ink-3">null</span> : str.length > 120 ? `${str.slice(0, 120)}…` : str}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && (
        <div className="px-2 py-1 text-[11px] text-ink-3 border-t border-line">{t('agent.rows_more', { n: rows.length - maxRows })}</div>
      )}
    </div>
  )
}

/** Agent-sent chart. Fetched with the bearer header (never a ?token= URL) and shown from an object URL. */
export function ChatImage({ url, caption }) {
  const { t } = useTranslation()
  const [state, setState] = useState({ src: null, error: false })
  useEffect(() => {
    if (!url) return undefined
    let cancelled = false
    let objectUrl = null
    api.fetchChatImage(url).then((res) => {
      if (cancelled) return
      if (res.success) {
        objectUrl = URL.createObjectURL(res.blob)
        setState({ src: objectUrl, error: false })
      } else {
        setState({ src: null, error: true })
      }
    })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])
  if (!url) return null
  if (state.error) return <div className="mt-1 text-xs text-bad-ink">{t('agent.image_load_failed')}</div>
  if (!state.src) return <div className="mt-1 text-xs text-ink-3">{t('common.loading')}</div>
  return <img src={state.src} alt={caption || t('agent.evt_image')} className="mt-1 max-h-64 rounded-control border border-line" />
}
