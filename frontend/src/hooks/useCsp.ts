import { useEffect, useState } from 'react'
import type { CspRequest, CspResponse, CspResult, CspError } from '../types/csp'
import { loadResultCache, saveResultCache, clearResultCache } from '../utils/resultCache'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseCspReturn {
  results: CspResult[]
  errors: CspError[]
  loading: boolean
  symbolCount: number
  isScanMode: boolean
  errorMessage: string | null
  cachedAt: number | null
  run: (req: CspRequest) => Promise<void>
  scan: (topN?: number, minDTE?: number, maxDTE?: number, universe?: string) => Promise<void>
}

export function useCsp(): UseCspReturn {
  const [results, setResults] = useState<CspResult[]>([])
  const [errors, setErrors] = useState<CspError[]>([])
  const [loading, setLoading] = useState(false)
  const [symbolCount, setSymbolCount] = useState(0)
  const [isScanMode, setIsScanMode] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [cachedAt, setCachedAt] = useState<number | null>(null)

  useEffect(() => {
    const entry = loadResultCache<{ results: CspResult[]; errors: CspError[] }>('csp')
    if (entry) {
      setResults(entry.data.results)
      setErrors(entry.data.errors)
      setCachedAt(entry.savedAt)
    }
  }, [])

  async function run(req: CspRequest) {
    setLoading(true)
    setIsScanMode(false)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    setErrors([])
    clearResultCache('csp')
    setSymbolCount(req.symbols.length)

    try {
      const response = await fetch(`${API_BASE}/api/screener/csp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      })

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string'
              ? body.detail
              : JSON.stringify(body.detail)
          }
        } catch {
          // ignore JSON parse failure
        }
        setErrorMessage(detail)
        return
      }

      const data: CspResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
      saveResultCache('csp', { results: data.results, errors: data.errors })
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
    clearResultCache('csp')
    setSymbolCount(0)

    try {
      const url = `${API_BASE}/api/screener/csp/scan?top_n=${topN}&min_dte=${minDTE}&max_dte=${maxDTE}&universe=${encodeURIComponent(universe)}`
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

      const data: CspResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
      saveResultCache('csp', { results: data.results, errors: data.errors })
      setCachedAt(Date.now())
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Network error — is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return { results, errors, loading, symbolCount, isScanMode, errorMessage, cachedAt, run, scan }
}
