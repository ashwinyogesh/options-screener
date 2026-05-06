export type InsightVerdict = 'ENTER' | 'WAIT' | 'SKIP'

export interface InsightResult {
  verdict: InsightVerdict
  confidence: number
  summary: string
  env_flag: string
  strike_flag: string
  key_risk: string
  reentry_condition: string | null
}

export interface InsightRequest {
  symbol: string
  price: number
  strike: number
  premium: number
  dte: number
  expiration: string
  env_score: number
  strike_score: number
  final_score: number
  env_detail: string
  strike_detail: string
  roc_annualized: number | null
  rsi: number
  iv_hv_ratio: number | null
  dist_from_52w_high_pct: number
}
