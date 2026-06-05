import { useCallback, useState } from 'react'
import type {
  DataCard,
  DDCoachError,
  DDEntry,
  FilingLinks,
  GuidedValuationInput,
  GuidedValuationResult,
  InsightType,
  IntelResult,
  PatchEntryInput,
  PathToTarget,
  ValuationOutput,
  ValuationRequest,
} from '../types/ddCoach'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface FetchResult<T> {
  data: T | null
  error: DDCoachError | null
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<FetchResult<T>> {
  try {
    const response = await fetch(url, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    })
    if (response.status === 204) {
      return { data: null, error: null }
    }
    if (response.status === 503) {
      let detail = 'DD Coach storage not yet provisioned.'
      try {
        const body = await response.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch { /* ignore */ }
      return { data: null, error: { detail, unavailable: true } }
    }
    if (!response.ok) {
      let detail = `Server error ${response.status}`
      try {
        const body = await response.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch { /* ignore */ }
      return { data: null, error: { detail, unavailable: false } }
    }
    const data = await response.json() as T
    return { data, error: null }
  } catch (err) {
    let detail = 'Network error — is the backend running?'
    if (err instanceof TypeError) {
      // Browsers throw "TypeError: Failed to fetch" when the request can't
      // reach the server at all (backend down, CORS, dropped wifi).
      detail = "Couldn't reach the backend. Is the FastAPI server running at "
        + `${API_BASE.replace(/\/+$/, '')}? (original error: ${err.message})`
    } else if (err instanceof Error && err.message) {
      detail = err.message
    }
    return { data: null, error: { detail, unavailable: false } }
  }
}

export interface UseDdCoachReturn {
  // Data card
  fetchDataCard: (ticker: string) => Promise<FetchResult<DataCard>>
  // Filings
  fetchFilings: (ticker: string) => Promise<FetchResult<FilingLinks>>
  // Path to target (Screen 6)
  fetchPathToTarget: (ticker: string, targetPrice: number) => Promise<FetchResult<PathToTarget>>
  // Filings intelligence (V3) — LLM-derived insights, cached server-side by accession#.
  fetchIntel: (ticker: string, insightType: InsightType, opts?: { force?: boolean }) => Promise<FetchResult<IntelResult>>
  // Valuation compute
  computeValuation: (req: ValuationRequest) => Promise<FetchResult<ValuationOutput>>
  // Guided valuation (V3 Fair Price screen)
  guidedValuation: (req: GuidedValuationInput) => Promise<FetchResult<GuidedValuationResult>>
  // Entry CRUD
  createEntry: (ticker: string) => Promise<FetchResult<DDEntry>>
  listEntries: (opts?: { ticker?: string; status?: 'draft' | 'completed'; limit?: number }) => Promise<FetchResult<{ items: DDEntry[]; count: number }>>
  getEntry: (id: string, ticker: string) => Promise<FetchResult<DDEntry>>
  patchEntry: (id: string, ticker: string, patch: PatchEntryInput) => Promise<FetchResult<DDEntry>>
  completeEntry: (id: string, ticker: string) => Promise<FetchResult<DDEntry>>
  loading: boolean
}

export function useDdCoach(): UseDdCoachReturn {
  const [loading, setLoading] = useState(false)

  const wrap = useCallback(async <T,>(fn: () => Promise<FetchResult<T>>): Promise<FetchResult<T>> => {
    setLoading(true)
    try {
      return await fn()
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchDataCard = useCallback(
    (ticker: string) => wrap(() => jsonFetch<DataCard>(
      `${API_BASE}/api/dd_coach/data_card/${encodeURIComponent(ticker.toUpperCase())}`,
    )),
    [wrap],
  )

  const fetchFilings = useCallback(
    (ticker: string) => wrap(() => jsonFetch<FilingLinks>(
      `${API_BASE}/api/dd_coach/filings/${encodeURIComponent(ticker.toUpperCase())}`,
    )),
    [wrap],
  )

  const fetchPathToTarget = useCallback(
    (ticker: string, targetPrice: number) => wrap(() => jsonFetch<PathToTarget>(
      `${API_BASE}/api/dd_coach/path_to_target/${encodeURIComponent(ticker.toUpperCase())}`
      + `?target_price=${encodeURIComponent(String(targetPrice))}`,
    )),
    [wrap],
  )

  const fetchIntel = useCallback(
    (ticker: string, insightType: InsightType, opts?: { force?: boolean }) => wrap(() => jsonFetch<IntelResult>(
      `${API_BASE}/api/dd_coach/intel/${encodeURIComponent(ticker.toUpperCase())}/${insightType}`
      + (opts?.force ? '?force=true' : ''),
    )),
    [wrap],
  )

  const computeValuation = useCallback(
    (req: ValuationRequest) => wrap(() => jsonFetch<ValuationOutput>(
      `${API_BASE}/api/dd_coach/valuation`,
      { method: 'POST', body: JSON.stringify(req) },
    )),
    [wrap],
  )

  const guidedValuation = useCallback(
    (req: GuidedValuationInput) => wrap(() => jsonFetch<GuidedValuationResult>(
      `${API_BASE}/api/dd_coach/guided_valuation`,
      { method: 'POST', body: JSON.stringify(req) },
    )),
    [wrap],
  )

  const createEntry = useCallback(
    (ticker: string) => wrap(() => jsonFetch<DDEntry>(
      `${API_BASE}/api/dd_coach/entries`,
      { method: 'POST', body: JSON.stringify({ ticker: ticker.toUpperCase() }) },
    )),
    [wrap],
  )

  const listEntries = useCallback(
    (opts?: { ticker?: string; status?: 'draft' | 'completed'; limit?: number }) => {
      const params = new URLSearchParams()
      if (opts?.ticker) params.set('ticker', opts.ticker.toUpperCase())
      if (opts?.status) params.set('status', opts.status)
      if (opts?.limit) params.set('limit', String(opts.limit))
      const qs = params.toString()
      return wrap(() => jsonFetch<{ items: DDEntry[]; count: number }>(
        `${API_BASE}/api/dd_coach/entries${qs ? `?${qs}` : ''}`,
      ))
    },
    [wrap],
  )

  const getEntry = useCallback(
    (id: string, ticker: string) => wrap(() => jsonFetch<DDEntry>(
      `${API_BASE}/api/dd_coach/entries/${encodeURIComponent(id)}?ticker=${encodeURIComponent(ticker.toUpperCase())}`,
    )),
    [wrap],
  )

  const patchEntry = useCallback(
    (id: string, ticker: string, patch: PatchEntryInput) => wrap(() => jsonFetch<DDEntry>(
      `${API_BASE}/api/dd_coach/entries/${encodeURIComponent(id)}?ticker=${encodeURIComponent(ticker.toUpperCase())}`,
      { method: 'PATCH', body: JSON.stringify(patch) },
    )),
    [wrap],
  )

  const completeEntry = useCallback(
    (id: string, ticker: string) => wrap(() => jsonFetch<DDEntry>(
      `${API_BASE}/api/dd_coach/entries/${encodeURIComponent(id)}/complete?ticker=${encodeURIComponent(ticker.toUpperCase())}`,
      { method: 'POST' },
    )),
    [wrap],
  )

  return {
    fetchDataCard,
    fetchFilings,
    fetchPathToTarget,
    fetchIntel,
    computeValuation,
    guidedValuation,
    createEntry,
    listEntries,
    getEntry,
    patchEntry,
    completeEntry,
    loading,
  }
}
