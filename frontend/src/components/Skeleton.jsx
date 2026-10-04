/**
 * Shared loading placeholder. Renders pulsing bars inside a card-like block and
 * keeps the loading label available to assistive tech (and tests) as sr-only text.
 */
export function SkeletonBar({ className = '' }) {
  return <div className={`skeleton h-3 ${className}`} aria-hidden="true" />
}

export default function Skeleton({ label, lines = 3, card = true, className = '' }) {
  return (
    <div
      role="status"
      aria-busy="true"
      className={`${card ? 'card' : ''} space-y-3 ${className}`}
    >
      <SkeletonBar className="w-1/3 h-4" />
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonBar key={i} className={i === lines - 1 ? 'w-2/3' : 'w-full'} />
      ))}
      {label ? <span className="sr-only">{label}</span> : null}
    </div>
  )
}
