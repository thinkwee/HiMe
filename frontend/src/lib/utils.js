/**
 * Ensure a timestamp string is treated as UTC.
 * Backend sends ISO strings without 'Z' suffix (for agent pandas compatibility),
 * but they are always UTC. Without 'Z', JS Date() interprets them as local time.
 */
const ensureUTC = (ts) => {
  if (typeof ts === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(ts)) {
    return ts + 'Z'
  }
  return ts
}

/**
 * Parse a backend timestamp into a Date object, treating bare ISO strings as UTC.
 */
export const parseBackendDate = (ts) => new Date(ensureUTC(ts))

/**
 * Format a timestamp as YYYY-MM-DD HH:MM:SS in the user's local timezone.
 * Returns '--' for falsy input, raw string for unparseable input.
 */
export const formatFullDateTime = (ts) => {
  if (!ts) return '--'
  const d = new Date(ensureUTC(ts))
  if (isNaN(d.getTime())) return String(ts)
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hours = String(d.getHours()).padStart(2, '0')
  const minutes = String(d.getMinutes()).padStart(2, '0')
  const seconds = String(d.getSeconds()).padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
}
