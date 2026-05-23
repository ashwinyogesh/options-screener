// Mirror of backend/routers/swing.py SwingResultOut.

export type SwingSetupType = 'breakout' | 'momentum' | 'reversion' | 'retest' | ''
export type SwingConfidence = 'high' | 'medium' | 'speculative'
export type SwingTriggerKind =
  | 'break_above'
  | 'pullback_to_ema8'
  | 'reclaim_confirm'
  | 'retest_of'
  | 'market_close'
  | ''

export interface SwingResult {
  symbol: string
  price: number
  setup_type: SwingSetupType
  setup_score: number
  swing_score: number
  confidence: SwingConfidence
  entry: number
  stop: number
  target: number
  risk_per_share: number
  reward_per_share: number
  rr: number
  hold_min_days: number
  hold_max_days: number
  trigger_kind: SwingTriggerKind
  extended: boolean
  drivers: string[]
  earnings_date: string | null
  earnings_warning: boolean
  rsi: number | null
  atr14: number | null
  adx: number | null
  rs_vs_spy: number | null
  ema_alignment_score: number | null
  ad_line_slope_pct: number | null
  institutional_ownership_pct: number | null
  bb_squeeze_pct: number | null
  consolidation_days: number | null
  consolidation_range_pct: number | null
  volume_surge_ratio: number | null
  higher_lows: number | null
  macd_inflection: boolean
  rsi_divergence: boolean
  fib_618_hold: boolean
  structure_reclaimed: boolean
  macd_hist_val?: number | null
  bb_position_val?: number | null
  setup_scores: Record<string, number>
  breakdown: Record<string, number>
  multipliers: Record<string, number>
  raw_score: number
  days_to_earnings: number | null
  forced_short_hold: boolean
  rr_gate: number
  regime_label: string
  narrative?: string | null
  risk_note?: string | null
  // --- v3 Lasso calibrated probability scorer ---
  swing_score_v2?: number
  swing_score_v3?: number
  p_target?: number | null
  lasso_confidence?: SwingConfidence
  lasso_top_features?: SwingLassoFeature[]
  lasso_missing_features?: string[]
  // --- composite: 30% v3.0 rank + 70% Lasso rank, 0-100 ---
  composite_score?: number
  adv_usd?: number
}

export interface SwingLassoFeature {
  name: string
  value: number
  std_value: number
  coef: number
  contribution: number
}

export type SwingScorerVersion = 'v2' | 'v3'

export interface RegimeState {
  index_trend: string
  vol_regime: string
  breadth_pct: number
  risk_appetite: number
  risk_on_score: number
  regime_label: 'risk_on' | 'neutral' | 'risk_off' | string
  rr_gate: number
  multiplier: number
  disable_setups: string[]
  drivers: string[]
  degraded: boolean
  spy_close: number
  spy_ema21: number
  spy_ema50: number
  vix: number
  vix_percentile: number
}

export interface SwingResponse {
  results: SwingResult[]
  scoring_version: string
  scoring_version_v3?: string
  regime?: RegimeState | null
  last_updated_at?: string | null
}

export interface SwingFilterState {
  setupType: SwingSetupType | 'all'
  minRR: number
  minScore: number
  excludeEarningsWarning: boolean
  minPrice: number
  minAdvM: number
}
