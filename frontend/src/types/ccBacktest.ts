export interface CcBacktestTrade {
  scan_date: string
  spot: number
  strike: number
  dte: number
  expiry_date: string
  delta: number
  premium: number
  final_score: number
  env_score: number
  strike_quant_score: number
  spot_at_exp: number
  assigned: number
  pnl_per_contract: number
  realised_roc_annualised: number
}

export interface CcBacktestBucket {
  bucket: string
  n: number
  mean_roc: number
  median_roc: number
  win_rate: number
  assign_rate: number
}

export interface CcBacktestSummary {
  n_trades: number
  n_winners: number
  n_losers: number
  n_assigned: number
  win_rate: number
  assign_rate: number
  mean_roc: number
  median_roc: number
  mean_score: number
  spearman_rho: number
  spearman_p: number
  monotone_buckets: boolean
  cutoff_delta_roc: number
  equity_curve: number[]
}

export interface CcBacktestResult {
  symbol: string
  years: number
  dte: number
  scan_start: string
  scan_end: string
  summary: CcBacktestSummary
  buckets: CcBacktestBucket[]
  trades: CcBacktestTrade[]
  caveats: string[]
}
