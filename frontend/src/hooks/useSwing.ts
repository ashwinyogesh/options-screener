import { useEffect, useState } from 'react'
import type { RegimeState, SwingResponse, SwingResult, SwingScorerVersion } from '../types/swing'
import { loadResultCache, saveResultCache, clearResultCache } from '../utils/resultCache'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseSwingReturn {
  results: SwingResult[]
  regime: RegimeState | null
  loading: boolean
  isScanMode: boolean
  gatesBypassed: boolean
  errorMessage: string | null
  cachedAt: number | null
  scoringVersion: string | null
  scoringVersionV3: string | null
  lastUpdatedAt: string | null
  scorerVersion: SwingScorerVersion
  setScorerVersion: (v: SwingScorerVersion) => void
  scan: (universe?: string) => Promise<void>
  run: (symbols: string[], bypassGates?: boolean) => Promise<void>
}

export function useSwing(): UseSwingReturn {
  const [results, setResults] = useState<SwingResult[]>([])
  const [regime, setRegime] = useState<RegimeState | null>(null)
  const [loading, setLoading] = useState(false)
  const [isScanMode, setIsScanMode] = useState(false)
  const [gatesBypassed, setGatesBypassed] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [cachedAt, setCachedAt] = useState<number | null>(null)
  const [scoringVersion, setScoringVersion] = useState<string | null>(null)
  const [scoringVersionV3, setScoringVersionV3] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null)
  const scorerVersion: SwingScorerVersion = 'v3'

  function setScorerVersion(_: SwingScorerVersion): void {
    // v2 is intentionally deprecated in the UI; keep signature stable for callers.
  }

  useEffect(() => {
    const entry = loadResultCache<{ results: SwingResult[]; scoringVersion: string | null; regime: RegimeState | null }>('swing')
    if (entry) {
      setResults(entry.data.results)
      setScoringVersion(entry.data.scoringVersion)
      setRegime(entry.data.regime ?? null)
      setCachedAt(entry.savedAt)
    }
  }, [])

  async function handleResponse(response: Response) {
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
    const data: SwingResponse = await response.json()
    setResults(data.results)
    setScoringVersion(data.scoring_version)
    setScoringVersionV3(data.scoring_version_v3 ?? null)
    setRegime(data.regime ?? null)
    setLastUpdatedAt(data.last_updated_at ?? null)
    saveResultCache('swing', { results: data.results, scoringVersion: data.scoring_version, regime: data.regime ?? null })
    setCachedAt(Date.now())
  }

  async function scan(universe: string = 'swing_eligible') {
    setLoading(true)
    setIsScanMode(true)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    clearResultCache('swing')
    try {
      const url = `${API_BASE}/api/screener/swing/scan?universe=${encodeURIComponent(universe)}`
      await handleResponse(await fetch(url, { method: 'GET' }))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  async function run(symbols: string[], bypassGates: boolean = true) {
    setLoading(true)
    setIsScanMode(false)
    setGatesBypassed(bypassGates)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    clearResultCache('swing')
    try {
      const response = await fetch(`${API_BASE}/api/screener/swing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols, bypass_gates: bypassGates }),
      })
      await handleResponse(response)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Network error — is the backend running?'
      setErrorMessage(msg)
    } finally {
      setLoading(false)
    }
  }

  return { results, regime, loading, isScanMode, gatesBypassed, errorMessage, cachedAt, scoringVersion, scoringVersionV3, lastUpdatedAt, scorerVersion, setScorerVersion, scan, run }
}
