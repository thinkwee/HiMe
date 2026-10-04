/**
 * Tiny external store for the monitor's *streaming* state (latest thought and
 * the reply being typed). Keeping it outside React state means a streamed
 * delta re-renders only the components subscribed to it (the live bubble and
 * the streaming reply), never the whole timeline.
 */
import { useSyncExternalStore } from 'react'

export function createLiveStore() {
  let state = { thought: '', replies: {} }
  const subs = new Set()
  const emit = () => subs.forEach((fn) => fn())
  return {
    get: () => state,
    subscribe(fn) {
      subs.add(fn)
      return () => subs.delete(fn)
    },
    setThought(thought) {
      if (state.thought === thought) return
      state = { ...state, thought }
      emit()
    },
    setReply(runKey, text) {
      const key = runKey || '_'
      if ((state.replies[key] || '') === text) return
      const replies = { ...state.replies }
      if (text) replies[key] = text
      else delete replies[key]
      state = { ...state, replies }
      emit()
    },
    clearReplies() {
      if (!Object.keys(state.replies).length) return
      state = { ...state, replies: {} }
      emit()
    },
    reset() {
      state = { thought: '', replies: {} }
      emit()
    },
  }
}

/** Subscribe to one primitive slice of the store. */
export function useLive(store, selector) {
  return useSyncExternalStore(store.subscribe, () => selector(store.get()), () => selector(store.get()))
}
