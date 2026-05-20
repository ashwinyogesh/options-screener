import { useCallback, useState } from 'react'
import type { CcBacktestResult } from '../types/ccBacktest'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseCcBacktestReturn {
  backtests: Map<string, CcBacktestResult>
  loading: Set<string>
  errors: Map<string, string>
  fetchBacktest: (symbol: string, years?: number, dte?: number) => Promise<void>
}

export function useCcBacktest(): UseCcBacktestReturn {
  const [backtests, setBacktests] = useState<Map<string, CcBacktestResult>>(new Map())
  const [loading, setLoading] = useState<Set<string>>(new Set())
  const [errors, setErrors] = useState<Map<string, string>>(new Map())

  const fetchBacktest = useCallback(async (symbol: string, years = 2, dte = 35) => {
    const key = `${symbol}:${years}:${dte}`
    setLoading(prev => new Set(prev).add(key))
    setErrors(prev => { const m = new Map(prev); m.delete(key); return m })

    try {
      const url = `${API_BASE}/api/screener/cc/${encodeURIComponent(symbol)}/backtest?years=${years}&dte=${dte}`
      const res = await fetch(url)
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Request failed' }))
        throw new Error(body.detail ?? 'Request failed')
      }
      const data: CcBacktestResult = await res.json()
      setBacktests(prev => { const m = new Map(prev); m.set(key, data); return m })
    } catch (e) {
      setErrors(prev => { const m = new Map(prev); m.set(key, String(e)); return m })
    } finally {
      setLoading(prev => { const s = new Set(prev); s.delete(key); return s })
    }
  }, [])

  return { backtests, loading, errors, fetchBacktest }
}
