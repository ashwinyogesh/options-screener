import { useState } from 'react'
import type { DcfData } from '../types/dcf'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export function useDcf() {
  const [data, setData] = useState<DcfData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function fetchTicker(ticker: string, refresh = false, trials = 5000) {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ ticker, trials: String(trials) })
      if (refresh) params.set('refresh', 'true')
      const url = `${API_BASE}/api/dcf?${params.toString()}`
      const r = await fetch(url)
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }))
        throw new Error(err.detail ?? 'Request failed')
      }
      const json = (await r.json()) as DcfData
      setData(json)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  return { data, loading, error, fetchTicker }
}
