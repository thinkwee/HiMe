/**
 * InlineFlash — small non-blocking status line (replaces alert()).
 * Pair with the `useFlash` hook from lib/hooks.
 */
import { AlertCircle, CheckCircle2 } from 'lucide-react'

export default function InlineFlash({ flash, className = '' }) {
  if (!flash) return null
  const isError = flash.type === 'error'
  return (
    <div
      role={isError ? 'alert' : 'status'}
      className={`flex items-start gap-1.5 rounded-chip px-2 py-1.5 text-xs ${
        isError ? 'bg-bad/10 text-bad-ink border border-bad/30' : 'bg-ok/10 text-ok-ink border border-ok/30'
      } ${className}`}
    >
      {isError ? <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-px" aria-hidden="true" /> : <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0 mt-px" aria-hidden="true" />}
      <span className="break-words min-w-0">{flash.text}</span>
    </div>
  )
}
