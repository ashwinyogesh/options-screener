import { useEffect, useState } from 'react'
import type { CcRequest, CcResponse, CcResult, CcError } from '../types/cc'
import { loadResultCache, saveResultCache, clearResultCache } from '../utils/resultCache'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseCcReturn {
  results: CcResult[]
  errors: CcError[]
  loading: boolean
  symbolCount: number
  isScanMode: boolean
  errorMessage: string | null
  cachedAt: number | null
  lastUpdatedAt: string | null
  vixLevel: number | null
  vixPercentile: number | null
  volRegime: string | null
  run: (req: CcRequest) => Promise<void>
  scan: (topN?: number, minDTE?: number, maxDTE?: number, universe?: string) => Promise<void>
}

export function useCc(): UseCcReturn {
  const [results, setResults] = useState<CcResult[]>([])
  const [errors, setErrors] = useState<CcError[]>([])
  const [loading, setLoading] = useState(false)
  const [symbolCount, setSymbolCount] = useState(0)
  const [isScanMode, setIsScanMode] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [cachedAt, setCachedAt] = useState<number | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null)
  const [vixLevel, setVixLevel] = useState<number | null>(null)
  const [vixPercentile, setVixPercentile] = useState<number | null>(null)
  const [volRegime, setVolRegime] = useState<string | null>(null)

  useEffect(() => {
    const entry = loadResultCache<{ results: CcResult[]; errors: CcError[] }>('cc')
    if (entry) {
      setResults(entry.data.results)
      setErrors(entry.data.errors)
      setCachedAt(entry.savedAt)
    }
  }, [])

  async function run(req: CcRequest) {
    setLoading(true)
    setIsScanMode(false)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    setErrors([])
    clearResultCache('cc')
    setSymbolCount(req.symbols.length)

    try {
      const response = await fetch(`${API_BASE}/api/screener/cc`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      })

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
          }
        } catch { /* ignore */ }
        setErrorMessage(detail)
        return
      }

      const data: CcResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
      setVixLevel(data.vix_level ?? null)
      setVixPercentile(data.vix_percentile ?? null)
      setVolRegime(data.vol_regime ?? null)
      saveResultCache('cc', { results: data.results, errors: data.errors })
      setCachedAt(Date.now())
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  async function scan(topN: number = 20, minDTE: number = 30, maxDTE: number = 45, universe: string = 'all') {
    setLoading(true)
    setIsScanMode(true)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    setErrors([])
    clearResultCache('cc')
    setSymbolCount(0)

    try {
      const url = `${API_BASE}/api/screener/cc/scan?top_n=${topN}&min_dte=${minDTE}&max_dte=${maxDTE}&universe=${encodeURIComponent(universe)}`
      const response = await fetch(url, { method: 'GET' })

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
          }
        } catch { /* ignore */ }
        setErrorMessage(detail)
        return
      }

      const data: CcResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
      setLastUpdatedAt(data.last_updated_at ?? null)
      setVixLevel(data.vix_level ?? null)
      setVixPercentile(data.vix_percentile ?? null)
      setVolRegime(data.vol_regime ?? null)
      saveResultCache('cc', { results: data.results, errors: data.errors })
      setCachedAt(Date.now())
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  return { results, errors, loading, symbolCount, isScanMode, errorMessage, cachedAt, lastUpdatedAt, vixLevel, vixPercentile, volRegime, run, scan }
}
