export interface CspStrikeInfo {
  strike: number
  delta: number
  premium: number
  annualized_return: number
  bid_ask_spread_pct: number | null
  env_score: number
  strike_score: number
  csp_score: number
  env_detail: string
  strike_detail: string
  is_best: boolean
  iv_fallback: boolean
  stale_premium: boolean
  iv_hv_ratio: number | null
}

export interface CspResult {
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
  vol_support_126_1: number | null
  vol_support_126_2: number | null
  vol_support_126_3: number | null
  dte: number
  expiration: string
  strikes: CspStrikeInfo[]
  best_csp_score: number
  using_hv_fallback: boolean
  expected_move: number
  dist_from_52w_high_pct: number
}

export interface CspExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  strikes: CspStrikeInfo[]
  best_score: number
  using_hv_fallback: boolean
  expected_move: number
}

export interface GroupedCspResult {
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
  vol_support_126_1: number | null
  vol_support_126_2: number | null
  vol_support_126_3: number | null
  best_score: number
  using_hv_fallback: boolean
  expirations: CspExpirationRow[]
  dist_from_52w_high_pct: number
  iv_hv_ratio: number | null
  env_detail: string
}

export interface CspError {
  symbol: string
  reason: string
}

export interface CspRequest {
  symbols: string[]
  minDTE: number
  maxDTE: number
}

export interface CspResponse {
  results: CspResult[]
  errors: CspError[]
}

export interface CspFilterState {
  smaRatioBullishOnly: boolean  // sma_ratio > 1
  maxSpreadPct: number          // 0 = no filter
  excludeEarningsWithinDte: boolean
  maxCollateral: number         // 0 = no filter; strike × 100 per contract
}
