export interface StrikeInfo {
  strike: number
  delta: number
  premium: number
  annualized_return: number
  bid_ask_spread_pct: number | null
  env_score: number
  strike_score: number
  csp_score: number
  is_best: boolean
  iv_fallback: boolean
  stale_premium: boolean
}

export interface ScreenerResult {
  symbol: string
  price: number
  bb_upper: number
  bb_middle: number
  bb_lower: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  vol_support_1: number | null
  vol_support_2: number | null
  vol_support_3: number | null
  dte: number
  expiration: string
  strikes: StrikeInfo[]
  best_csp_score: number
  using_hv_fallback: boolean
  expected_move: number
}

export interface ExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  strikes: StrikeInfo[]
  best_score: number
  using_hv_fallback: boolean
  expected_move: number
}

export interface GroupedScreenerResult {
  symbol: string
  price: number
  bb_upper: number
  bb_middle: number
  bb_lower: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  vol_support_1: number | null
  vol_support_2: number | null
  vol_support_3: number | null
  best_score: number
  using_hv_fallback: boolean
  expirations: ExpirationRow[]
}

export interface ScreenerError {
  symbol: string
  reason: string
}

export interface ScreenerRequest {
  symbols: string[]
  minDTE: number
  maxDTE: number
}

export interface ScreenerResponse {
  results: ScreenerResult[]
  errors: ScreenerError[]
}

export interface FilterState {
  smaRatioBullishOnly: boolean  // sma_ratio > 1
  maxSpreadPct: number          // 0 = no filter
  excludeEarningsWithinDte: boolean
  maxCollateral: number         // 0 = no filter; strike × 100 per contract
}
