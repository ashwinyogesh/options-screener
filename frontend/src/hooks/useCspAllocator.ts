import { useMemo, useState } from 'react'
import type { CspResult } from '../types/csp'
import type { AllocationResult, AllocatorSettings } from '../types/cspAllocator'
import { allocateCsp } from '../utils/cspAllocator'

export const DEFAULT_ALLOCATOR_SETTINGS: AllocatorSettings = {
  mode: 'edge',
  riskTolerance: 0.5,   // Balanced
  minScore: 69,          // v3.4 "take it" cliff (SCORING_REFERENCE.md)
  maxNamePct: 0.35,      // Optimized mode: cap any single name at 35% of capital
  diversification: 0.15, // Optimized mode: mild concave merit — spreads across near-tied names
}

interface UseCspAllocatorReturn {
  capital: number
  setCapital: (v: number) => void
  settings: AllocatorSettings
  setSettings: (s: AllocatorSettings) => void
  result: AllocationResult | null
}

/**
 * Owns capital + settings state and derives the allocation basket from the
 * CSP contracts currently on screen. Pure derivation — no network.
 */
export function useCspAllocator(results: CspResult[]): UseCspAllocatorReturn {
  const [capital, setCapital] = useState(0)
  const [settings, setSettings] = useState<AllocatorSettings>(DEFAULT_ALLOCATOR_SETTINGS)

  const result = useMemo<AllocationResult | null>(() => {
    if (capital <= 0 || results.length === 0) return null
    return allocateCsp(results, capital, settings)
  }, [results, capital, settings])

  return { capital, setCapital, settings, setSettings, result }
}
