export interface DitmStrikeInfo {
  strike: number
  delta: number
  mid: number
  extrinsic_pct: number           // extrinsic / strike as % (lower = better)
  theta_annualized_pct: number    // |BS theta annual| / strike * 100 (lower = better)
  breakeven_pct: number           // (strike + mid - price) / price * 100 — display only
  capital_efficiency_pct: number  // mid / price * 100 — sweet spot 25–35%
  bid_ask_spread_pct: number | null
  chain_oi: number
  env_score: number               // v4: pillar percentile (val+cap+macro), 0–100
  strike_score: number            // v4: pillar percentile (tech+option), 0–100
  ditm_score: number              // v4: cross-sectional percentile, 0–100
  env_detail: string              // v4: "Val:+X Cap:+Y Macro:+Z" group contribution string
  strike_detail: string           // v4: "Tech:+X Opt:+Y" group contribution string
  is_best: boolean
  iv_fallback: boolean            // true when HV30 used instead of chain IV
  // v4 (ADR-0032)
  tier: 'A' | 'B' | 'C' | 'D' | 'E' | null
  score_v4: number | null         // mirror of ditm_score after v4 pass
  factor_breakdown: Record<string, number> | null  // signed contribution per factor name
}

export interface DitmResult {
  symbol: string
  price: number
  sma_ratio: number               // SMA50 / SMA200
  hv_rank: number                 // HV percentile 0–100
  hv30: number                    // 30-day HV as % (e.g. 28.5)
  weekly_rsi: number              // Weekly RSI(14)
  ret_200d: number                // 200d median-anchored return as % (e.g. 18.5)
  dist_from_52w_high_pct: number  // % below 52W high (negative = below)
  earnings_date: string | null
  days_to_earnings: number | null
  earnings_within_dte: boolean
  dte: number
  expiration: string
  strikes: DitmStrikeInfo[]
  best_ditm_score: number
  gap_3d_pct: number              // max overnight gap last 3 sessions (%)
  macro_hold: boolean             // VIX ≥ 25 and rising, or SPY < SMA200
  chain_median_oi: number
  iv_percentile: number | null    // v3 strike-side vol-cheapness factor (0–100, HV-based)
  trend_r2: number | null         // v3.2: R² of 50-day OLS price regression (0–1)
  best_tier: 'A' | 'B' | 'C' | 'D' | 'E' | null  // v4 tier of the best strike
}

export interface DitmExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  strikes: DitmStrikeInfo[]
  best_score: number
  macro_hold: boolean
  chain_median_oi: number
}

export interface GroupedDitmResult {
  symbol: string
  price: number
  sma_ratio: number
  hv_rank: number
  hv30: number
  weekly_rsi: number
  ret_200d: number
  dist_from_52w_high_pct: number
  earnings_date: string | null
  days_to_earnings: number | null
  earnings_within_dte: boolean
  gap_3d_pct: number
  macro_hold: boolean
  best_score: number
  expirations: DitmExpirationRow[]
  env_detail: string
  iv_percentile: number | null    // v3 — surface to the IV%ile column
  trend_r2: number | null         // v3.2: R² of 50-day OLS price regression
  best_tier: 'A' | 'B' | 'C' | 'D' | 'E' | null  // v4 tier of the best strike
}

export interface DitmFilterState {
  smaRatioBullishOnly: boolean
  maxSpreadPct: number
  excludeEarningsWithinDte: boolean
  maxCapital: number
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
  macro_pass: boolean
  vix_level: number | null
  vix_5d_change: number | null
  spy_above_sma200: boolean
  last_updated_at?: string | null
}
