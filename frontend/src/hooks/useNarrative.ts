import { useCallback, useEffect, useState } from 'react'
import type { AcsScore, NarrativeAlert, NarrativeError } from '../types/narrative'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseNarrativeReturn {
  top: AcsScore[]
  emerging: AcsScore[]
  alerts: NarrativeAlert[]
  loading: boolean
  error: NarrativeError | null
  refresh: () => Promise<void>
}

async function safeFetch<T>(url: string): Promise<{ data: T | null; error: NarrativeError | null }> {
  try {
    const response = await fetch(url, { method: 'GET' })
    if (response.status === 503) {
      let detail = 'Narrative platform not yet provisioned.'
      try {
        const body = await response.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch { /* ignore */ }
      return { data: null, error: { detail, unavailable: true } }
    }
    if (!response.ok) {
      return { data: null, error: { detail: `Server error ${response.status}`, unavailable: false } }
    }
    const data = await response.json() as T
    return { data, error: null }
  } catch (err: unknown) {
    const detail = err instanceof Error ? err.message : 'Network error — is the backend running?'
    return { data: null, error: { detail, unavailable: false } }
  }
}

export function useNarrative(): UseNarrativeReturn {
  const [top, setTop] = useState<AcsScore[]>([])
  const [emerging, setEmerging] = useState<AcsScore[]>([])
  const [alerts, setAlerts] = useState<NarrativeAlert[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<NarrativeError | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [topRes, emergingRes, alertsRes] = await Promise.all([
      safeFetch<AcsScore[]>(`${API_BASE}/api/narrative/tickers/top?limit=50`),
      safeFetch<AcsScore[]>(`${API_BASE}/api/narrative/emerging?limit=50`),
      safeFetch<NarrativeAlert[]>(`${API_BASE}/api/narrative/alerts?limit=50`),
    ])
    // If any sibling reports 503, surface it once — they will all be 503 in Phase 0.
    const firstError = topRes.error ?? emergingRes.error ?? alertsRes.error
    if (firstError) setError(firstError)
    setTop(topRes.data ?? [])
    setEmerging(emergingRes.data ?? [])
    setAlerts(alertsRes.data ?? [])
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return { top, emerging, alerts, loading, error, refresh }
}
