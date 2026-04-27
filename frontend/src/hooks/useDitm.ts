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
  macroPass: boolean
  vixLevel: number | null
  vix5dChange: number | null
  spyAboveSma200: boolean
  run: (req: DitmRequest) => Promise<void>
  scan: (topN?: number, minDTE?: number, maxDTE?: number, universe?: string) => Promise<void>
}

export function useDitm(): UseDitmReturn {
  const [results, setResults] = useState<DitmResult[]>([])
  const [errors, setErrors] = useState<DitmError[]>([])
  const [loading, setLoading] = useState(false)
  const [symbolCount, setSymbolCount] = useState(0)
  const [isScanMode, setIsScanMode] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [macroPass, setMacroPass] = useState(true)
  const [vixLevel, setVixLevel] = useState<number | null>(null)
  const [vix5dChange, setVix5dChange] = useState<number | null>(null)
  const [spyAboveSma200, setSpyAboveSma200] = useState(true)

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
            detail = typeof body.detail === 'string'
              ? body.detail
              : JSON.stringify(body.detail)
          }
        } catch { /* ignore */ }
        setErrorMessage(detail)
        return
      }

      const data: DitmResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
      setMacroPass(data.macro_pass)
      setVixLevel(data.vix_level)
      setVix5dChange(data.vix_5d_change)
      setSpyAboveSma200(data.spy_above_sma200)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  async function scan(topN: number = 20, minDTE: number = 180, maxDTE: number = 365, universe: string = 'all') {
    setLoading(true)
    setIsScanMode(true)
    setErrorMessage(null)
    setResults([])
    setErrors([])
    setSymbolCount(0)

    try {
      const url = `${API_BASE}/api/screener/ditm/scan?top_n=${topN}&min_dte=${minDTE}&max_dte=${maxDTE}&universe=${encodeURIComponent(universe)}`
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

      const data: DitmResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
      setMacroPass(data.macro_pass)
      setVixLevel(data.vix_level)
      setVix5dChange(data.vix_5d_change)
      setSpyAboveSma200(data.spy_above_sma200)
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Network error — is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return { results, errors, loading, symbolCount, isScanMode, errorMessage, macroPass, vixLevel, vix5dChange, spyAboveSma200, run, scan }
}
