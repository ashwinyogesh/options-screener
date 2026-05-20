import { useCallback, useEffect, useRef, useState } from 'react'
import type { NarrativeError } from '../types/narrative'
import type { SignalsFilters, SignalsResponse } from '../types/signals'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const REFRESH_INTERVAL_MS = 5 * 60 * 1000  // 5 min — matches useNarrative cadence

const EMPTY_RESPONSE: SignalsResponse = {
  n_total: 0,
  horizons: [
    { horizon_days: 5, n_complete: 0, hit_rate: null, median_excess_return: null },
    { horizon_days: 10, n_complete: 0, hit_rate: null, median_excess_return: null },
    { horizon_days: 20, n_complete: 0, hit_rate: null, median_excess_return: null },
  ],
  events: [],
}

interface UseSignalsReturn {
  data: SignalsResponse
  loading: boolean
  error: NarrativeError | null
  lastUpdatedAt: Date | null
  filters: SignalsFilters
  setFilters: (next: SignalsFilters) => void
  refresh: () => Promise<void>
}

async function fetchSignals(
  filters: SignalsFilters,
): Promise<{ data: SignalsResponse | null; error: NarrativeError | null }> {
  const params = new URLSearchParams()
  if (filters.since) params.set('since', filters.since)
  if (filters.minConfidence != null) params.set('min_confidence', filters.minConfidence.toString())
  if (filters.transition) params.set('transition', filters.transition)
  if (filters.ticker) params.set('ticker', filters.ticker.toUpperCase())
  params.set('limit', '200')

  try {
    const response = await fetch(`${API_BASE}/api/narrative/signals?${params.toString()}`)
    if (response.status === 503) {
      let detail = 'Signal log not yet populated.'
      try {
        const body = await response.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch { /* ignore */ }
      return { data: null, error: { detail, unavailable: true } }
    }
    if (!response.ok) {
      return { data: null, error: { detail: `Server error ${response.status}`, unavailable: false } }
    }
    const data = await response.json() as SignalsResponse
    return { data, error: null }
  } catch (err: unknown) {
    const detail = err instanceof Error ? err.message : 'Network error — is the backend running?'
    return { data: null, error: { detail, unavailable: false } }
  }
}

const DEFAULT_FILTERS: SignalsFilters = {
  since: null,
  minConfidence: 0.5,
  transition: null,
  ticker: null,
}

export function useSignals(): UseSignalsReturn {
  const [data, setData] = useState<SignalsResponse>(EMPTY_RESPONSE)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<NarrativeError | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)
  const [filters, setFilters] = useState<SignalsFilters>(DEFAULT_FILTERS)
  const intervalRef = useRef<number | null>(null)
  // Track the latest filters in a ref so the interval always reads current values
  // without re-installing on every filter change.
  const filtersRef = useRef(filters)
  filtersRef.current = filters

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    const { data: next, error: err } = await fetchSignals(filtersRef.current)
    if (err) setError(err)
    // Preserve previous rows on transient errors so the UI does not flash empty.
    if (next !== null) {
      setData(next)
      setLastUpdatedAt(new Date())
    }
    setLoading(false)
  }, [])

  // Refetch immediately whenever filters change.
  useEffect(() => {
    void refresh()
  }, [filters, refresh])

  useEffect(() => {
    intervalRef.current = window.setInterval(() => {
      void refresh()
    }, REFRESH_INTERVAL_MS)
    return () => {
      if (intervalRef.current != null) window.clearInterval(intervalRef.current)
    }
  }, [refresh])

  return { data, loading, error, lastUpdatedAt, filters, setFilters, refresh }
}
