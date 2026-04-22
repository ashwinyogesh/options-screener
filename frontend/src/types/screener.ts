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
  strike: number
  strike_is_fallback: boolean
  strike_mid: number
  strike_mid_is_fallback: boolean
  vol_support_1: number | null
  vol_support_2: number | null
  vol_support_3: number | null
  delta: number
  delta_mid: number
  bid_ask_spread_pct: number | null
  csp_score: number
  dte: number
  expiration: string
  premium: number
  premium_mid: number
  collateral: number
  return_pct: number
  annualized_return: number
  return_pct_mid: number
  annualized_return_mid: number
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
  minRsi: number
  maxRsi: number
  minIvRank: number
  smaRatioBullishOnly: boolean  // sma_ratio > 1
  minDelta: number
  maxDelta: number
  maxSpreadPct: number          // 0 = no filter
  excludeEarningsWithinDte: boolean
}
