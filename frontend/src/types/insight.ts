export type InsightVerdict = 'ENTER' | 'WAIT' | 'SKIP'
export type StockCycle = 'Bear' | 'Normal' | 'Bull'

export interface InsightResult {
  reasoning: string
  verdict: InsightVerdict
  confidence: number
  summary: string
  regime_drivers: string
  current_regime: string
  stock_cycle: StockCycle
  bear_band: string
  normal_band: string
  bull_band: string
  ownership_case: string
  key_risk: string
  vix_regime: string
}

export interface InsightRequest {
  symbol: string
  price: number
  strike: number
  premium: number
  dte: number
  expiration: string
  earnings_within_dte: boolean
  env_score: number
  strike_score: number
  final_score: number
  env_detail: string
  strike_detail: string
  roc_annualized: number | null
  rsi: number
  iv_hv_ratio: number | null
  iv_percentile: number | null
  dist_from_52w_high_pct: number
}
