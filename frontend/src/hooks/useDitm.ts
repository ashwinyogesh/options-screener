import { useState } from 'react'
import type { DitmRequest, DitmResponse, DitmResult, DitmError } from '../types/ditm'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseDitmReturn {
  results: DitmResult[]
  errors: DitmError[]
  loading: boolean
  symbolCount: number
  isScanMode: boolean
  errorMessage: string | null
  run: (req: DitmRequest) => Promise<void>
  scan: (topN?: number, minDTE?: number, maxDTE?: number) => Promise<void>
}

export function useDitm(): UseDitmReturn {
  const [results, setResults] = useState<DitmResult[]>([])
  const [errors, setErrors] = useState<DitmError[]>([])
  const [loading, setLoading] = useState(false)
  const [symbolCount, setSymbolCount] = useState(0)
  const [isScanMode, setIsScanMode] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function run(req: DitmRequest) {
    setLoading(true)
    setIsScanMode(false)
    setErrorMessage(null)
    setResults([])
    setErrors([])
    setSymbolCount(req.symbols.length)

    try {
      const response = await fetch(`${API_BASE}/api/screener/ditm`, {
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

      const data: DitmResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  async function scan(topN = 15, minDTE = 180, maxDTE = 365) {
    setLoading(true)
    setIsScanMode(true)
    setErrorMessage(null)
    setResults([])
    setErrors([])
    setSymbolCount(0)

    try {
      const url = `${API_BASE}/api/screener/ditm/scan?topN=${topN}&minDTE=${minDTE}&maxDTE=${maxDTE}`
      const response = await fetch(url)

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

      const data: DitmResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  return { results, errors, loading, symbolCount, isScanMode, errorMessage, run, scan }
}
