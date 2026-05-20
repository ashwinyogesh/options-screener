export interface CcStrikeInfo {
  strike: number
  delta: number
  premium: number
  annualized_return: number
  bid_ask_spread_pct: number | null
  env_score: number
  strike_score: number
  cc_score: number
  env_detail: string
  strike_detail: string
  is_best: boolean
  iv_fallback: boolean
  stale_premium: boolean
  iv_hv_ratio: number | null
  dist_pct: number | null
  em_buffer_pct: number | null
  otm_pct: number
  lq_count: number
  roc_annualized: number | null
  iv_stale: boolean
}

export interface CcResult {
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
  vol_resistance_126_1: number | null
  vol_resistance_126_2: number | null
  vol_resistance_126_3: number | null
  dte: number
  expiration: string
  strikes: CcStrikeInfo[]
  best_cc_score: number
  using_hv_fallback: boolean
  expected_move: number
  dist_from_52w_high_pct: number
  chain_median_oi: number
}

export interface CcExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  strikes: CcStrikeInfo[]
  best_score: number
  using_hv_fallback: boolean
  expected_move: number
  chain_median_oi: number
}

export interface GroupedCcResult {
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
  vol_resistance_126_1: number | null
  vol_resistance_126_2: number | null
  vol_resistance_126_3: number | null
  best_score: number
  using_hv_fallback: boolean
  expirations: CcExpirationRow[]
  dist_from_52w_high_pct: number
  iv_hv_ratio: number | null
  env_detail: string
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
  last_updated_at?: string | null
  vix_level?: number | null
  vix_percentile?: number | null
  vol_regime?: string | null
}

export interface CcFilterState {
  smaRatioBullishOnly: boolean
  maxSpreadPct: number
  excludeEarningsWithinDte: boolean
  maxCollateral: number   // strike × 100 notional
}
