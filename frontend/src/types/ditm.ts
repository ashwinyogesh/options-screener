export interface DitmStrikeInfo {
  strike: number
  delta: number
  premium: number
  intrinsic: number
  extrinsic: number
  extrinsic_pct: number
  moneyness_pct: number
  leverage: number
  bid_ask_spread_pct: number | null
  env_score: number
  strike_score: number
  ditm_score: number
  is_best: boolean
  iv_fallback: boolean
  stale_premium: boolean
}

export interface DitmResult {
  symbol: string
  price: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  vol_resistance_1: number | null
  vol_resistance_2: number | null
  vol_resistance_3: number | null
  dte: number
  expiration: string
  strikes: DitmStrikeInfo[]
  best_ditm_score: number
  using_hv_fallback: boolean
}

export interface DitmExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  strikes: DitmStrikeInfo[]
  best_score: number
  using_hv_fallback: boolean
}

export interface GroupedDitmResult {
  symbol: string
  price: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  vol_resistance_1: number | null
  vol_resistance_2: number | null
  vol_resistance_3: number | null
  best_score: number
  using_hv_fallback: boolean
  expirations: DitmExpirationRow[]
}

export interface DitmError {
  symbol: string
  reason: string
}

export interface DitmRequest {
  symbols: string[]
  minDTE: number
  maxDTE: number
}

export interface DitmResponse {
  results: DitmResult[]
  errors: DitmError[]
}

export interface DitmFilterState {
  minDelta: number
  maxExtrinsicPct: number
  smaRatioBullishOnly: boolean
  maxSpreadPct: number
  excludeEarningsWithinDte: boolean
}
