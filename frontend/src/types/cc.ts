export interface CcStrikeInfo {
  strike: number
  delta: number
  premium: number
  annualized_return: number
  bid_ask_spread_pct: number | null
  env_score: number
  strike_score: number
  cc_score: number
  is_best: boolean
  iv_fallback: boolean
  stale_premium: boolean
}

export interface CcResult {
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
  strikes: CcStrikeInfo[]
  best_cc_score: number
  using_hv_fallback: boolean
  expected_move: number
}

export interface CcExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  strikes: CcStrikeInfo[]
  best_score: number
  using_hv_fallback: boolean
  expected_move: number
}

export interface GroupedCcResult {
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
  expirations: CcExpirationRow[]
}

export interface CcError {
  symbol: string
  reason: string
}

export interface CcRequest {
  symbols: string[]
  minDTE: number
  maxDTE: number
}

export interface CcResponse {
  results: CcResult[]
  errors: CcError[]
}

export interface CcFilterState {
  smaRatioBullishOnly: boolean
  maxSpreadPct: number
  excludeEarningsWithinDte: boolean
  maxCollateral: number   // strike × 100 notional
}
