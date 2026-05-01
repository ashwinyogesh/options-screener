/**
 * Thin localStorage wrapper for persisting screener results across page refreshes.
 *
 * Keys are namespaced under `screener:` to avoid collisions.
 * A TTL check on read ensures stale data is never surfaced.
 */

const TTL_MS = 30 * 60 * 1000 // 30 minutes

interface CacheEntry<T> {
  data: T
  savedAt: number
}

export type CacheKey = 'csp' | 'cc' | 'ditm'

export function saveResultCache<T>(key: CacheKey, data: T): void {
  try {
    const entry: CacheEntry<T> = { data, savedAt: Date.now() }
    localStorage.setItem(`screener:${key}`, JSON.stringify(entry))
  } catch {
    // Quota exceeded or private-browsing restriction — silently skip.
  }
}

export function loadResultCache<T>(key: CacheKey, ttlMs = TTL_MS): CacheEntry<T> | null {
  try {
    const raw = localStorage.getItem(`screener:${key}`)
    if (!raw) return null
    const entry = JSON.parse(raw) as CacheEntry<T>
    if (typeof entry !== 'object' || entry === null || typeof entry.savedAt !== 'number') {
      localStorage.removeItem(`screener:${key}`)
      return null
    }
    if (Date.now() - entry.savedAt > ttlMs) {
      localStorage.removeItem(`screener:${key}`)
      return null
    }
    return entry
  } catch {
    return null
  }
}

export function clearResultCache(key: CacheKey): void {
  try {
    localStorage.removeItem(`screener:${key}`)
  } catch {
    // ignore
  }
}
