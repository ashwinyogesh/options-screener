// Keep in sync with backend/services/universe.py UNIVERSES.

export type UniverseKey =
  | 'all'
  | 'ai_full'
  | 'ai_energy'
  | 'ai_chips'
  | 'ai_infrastructure'
  | 'ai_models'
  | 'ai_applications'
  | 'stable_csp'

export interface UniverseOption {
  key: UniverseKey
  label: string
  size: number
  hint: string
}

// Sizes mirror the backend resolved lists. Update if you edit AI_BUILDOUT or _STABLE_CSP.
export const UNIVERSE_OPTIONS: UniverseOption[] = [
  { key: 'all',                label: 'Full universe (113)',          size: 113, hint: 'All AI buckets + fintech / growth / healthcare' },
  { key: 'stable_csp',         label: 'Stable CSP (29)',              size: 29,  hint: 'Financials, defensives, industrials — tight spreads, RSI stability, IV/HV 1.1–1.3' },
  { key: 'ai_full',            label: 'AI Buildout — full (94)',      size: 94,  hint: 'Energy + chips + infra + models + apps, deduped' },
  { key: 'ai_energy',          label: '↳ Energy (18)',                size: 18,  hint: 'Nuclear, gas, grid, power mgmt, datacenter cooling' },
  { key: 'ai_chips',           label: '↳ Chips (24)',                 size: 24,  hint: 'Silicon, foundry, equipment, optics, connectivity' },
  { key: 'ai_infrastructure',  label: '↳ Infrastructure (16)',        size: 16,  hint: 'Servers, networking, storage, DC REITs, GPU clouds' },
  { key: 'ai_models',          label: '↳ Models (8)',                 size: 8,   hint: 'Foundation-model exposure (US + China)' },
  { key: 'ai_applications',    label: '↳ Applications (28)',          size: 28,  hint: 'AI inside product (data, security, dev, vertical)' },
]

export const DEFAULT_UNIVERSE: UniverseKey = 'all'

export function universeSize(key: UniverseKey): number {
  return UNIVERSE_OPTIONS.find(o => o.key === key)?.size ?? 0
}
