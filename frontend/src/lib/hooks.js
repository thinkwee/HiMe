/**
 * Small shared hooks.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

function subscribeVisibility(cb) {
  document.addEventListener('visibilitychange', cb)
  return () => document.removeEventListener('visibilitychange', cb)
}

/** True while the browser tab is visible. */
export function useDocumentVisible() {
  return useSyncExternalStore(
    subscribeVisibility,
    () => document.visibilityState !== 'hidden',
    () => true,
  )
}

/**
 * Run `fn` every `ms` while `enabled` is true AND the tab is visible.
 * Does not run `fn` immediately (callers fetch on their own); when the tab
 * becomes visible again after being hidden, `fn` fires once to catch up.
 */
export function usePolling(fn, ms, enabled = true) {
  const visible = useDocumentVisible()
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  }, [fn])
  const wasPausedRef = useRef(false)

  useEffect(() => {
    if (!enabled || !visible) {
      wasPausedRef.current = true
      return undefined
    }
    if (wasPausedRef.current) {
      wasPausedRef.current = false
      fnRef.current()
    }
    const id = setInterval(() => fnRef.current(), ms)
    return () => clearInterval(id)
  }, [ms, enabled, visible])
}

/**
 * Calls `fn` each time `active` flips from false to true (not on first mount
 * — pages do their own initial load). Used to refetch when a kept-mounted
 * page becomes the active route again.
 */
export function useOnActivate(active, fn) {
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  }, [fn])
  const prevRef = useRef(active)
  useEffect(() => {
    if (active && !prevRef.current) fnRef.current()
    prevRef.current = active
  }, [active])
}

/**
 * Transient inline message: `[flash, setFlash]` where `flash` is
 * `{ type: 'error' | 'success', text }` or null. Auto-clears after `ms` and
 * cleans its timer up on unmount (replaces blocking alert()).
 */
export function useFlash(ms = 5000) {
  const [flash, setFlashState] = useState(null)
  const timerRef = useRef(null)
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])
  const setFlash = useCallback((type, text) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = null
    if (!type) {
      setFlashState(null)
      return
    }
    setFlashState({ type, text })
    if (ms > 0) {
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        setFlashState(null)
      }, ms)
    }
  }, [ms])
  return [flash, setFlash]
}
