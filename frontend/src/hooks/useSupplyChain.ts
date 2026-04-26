import { useState } from 'react'
import type { SupplyChainData } from '../types/supplyChain'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export function useSupplyChain() {
  const [data, setData] = useState<SupplyChainData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function fetchTicker(
    ticker: string,
    refresh = false,
    enrichIndustry = true,
  ) {
    setLoading(true)
    setError(null)
    try {
      const enrich = enrichIndustry ? 'filing+industry' : 'filing'
      const params = new URLSearchParams({ ticker, enrich })
      if (refresh) params.set('refresh', 'true')
      const url = `${API_BASE}/api/supply-chain?${params.toString()}`
      const r = await fetch(url)
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }))
        throw new Error(err.detail ?? 'Request failed')
      }
      const json = (await r.json()) as SupplyChainData
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
