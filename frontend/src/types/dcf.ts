// DCF API contract — must match backend/services/dcf_service.py

export interface WaccBuildup {
  risk_free_rate: number
  equity_risk_premium: number
  beta: number
  cost_of_equity: number
  pretax_cost_of_debt: number
  after_tax_cost_of_debt: number
  weight_equity: number
  weight_debt: number
  wacc: number
}

export interface DcfGrounding {
  ticker: string
  company_name: string
  current_price: number
  market_cap: number | null
  shares_out: number | null
  net_debt: number | null
  total_debt: number | null
  cash: number | null
  beta: number | null
  revenue_ttm: number | null
  revenue_history: Array<{ year: number; revenue: number }>
  revenue_cagr_5y: number | null
  operating_margin_ttm: number | null
  operating_margin_history: Array<{ year: number; margin: number }>
  gross_margin_ttm: number | null
  rnd_pct_revenue: number | null
  ebitda_ttm: number | null
  tax_rate: number | null
  sector: string | null
  industry: string | null
  buyback_yield: number | null
  sbc_pct_revenue: number | null
  sbc_dilution_yield: number | null
  net_buyback_yield: number | null
  share_history: Array<{ year: number; shares: number }>
  roic: number | null
  forward_pe_market: number | null
  ev_ebitda_market: number | null
  wacc_buildup: WaccBuildup
  as_of: string
}

export type AssumptionKey =
  | 'revenue_growth'
  | 'operating_margin'
  | 'discount_rate'
  | 'terminal_growth'
  | 'capex_pct_revenue'

export interface ScenarioAssumption {
  label: 'Conservative' | 'Base' | 'Optimistic'
  revenue_growth: number
  operating_margin: number
  wacc_risk_adj_bps: number
  discount_rate: number
  terminal_growth: number
  capex_pct_revenue: number
  rationale: Record<AssumptionKey, string>
  strongest_driver: AssumptionKey
  narrative: string
}

export interface ScenarioResult {
  label: 'Conservative' | 'Base' | 'Optimistic'
  fair_value_per_share: number
  upside_pct: number
  enterprise_value: number
  equity_value: number
  pv_of_fcfs: number
  pv_of_terminal: number
}

export interface MonteCarloResult {
  trials: number
  percentiles: { p25: number; p40: number; p50: number; p60: number; p75: number }
  mean: number
  std: number
  prob_above_current: number
  histogram: { bin_edges: number[]; counts: number[] }
  sample: number[]
}

export interface ReverseDcfResult {
  implied_revenue_growth: number | null
  base_revenue_growth: number
  delta_vs_base: number | null
  interpretation: string
}

export interface SensitivityMatrix {
  wacc_axis: number[]
  terminal_growth_axis: number[]
  grid: number[][]
  base_wacc: number
  base_terminal_growth: number
}

export type Recommendation = 'STRONG_BUY' | 'BUY' | 'HOLD' | 'AVOID' | 'STRONG_AVOID'

export interface Verdict {
  recommendation: Recommendation
  suggested_entry_price: number
  suggested_exit_price: number
  confidence: number
  key_assumption_to_monitor: string
  margin_of_safety_pct: number
}

export type MultiplesFlag = 'aligned' | 'model_conservative' | 'model_aggressive' | 'insufficient_data'

export interface MultiplesCheck {
  implied_forward_pe: number | null
  market_forward_pe: number | null
  pe_delta_pct: number | null
  implied_ev_ebitda: number | null
  market_ev_ebitda: number | null
  ev_ebitda_delta_pct: number | null
  diagnostic: string
  flag: MultiplesFlag
}

export interface RoicFlag {
  roic: number | null
  wacc: number
  spread: number | null
  base_terminal_growth: number
  triggered: boolean
  message: string
}

export interface Distribution {
  shape: 'normal' | 'triangular' | 'uniform'
  params: Record<string, number>
}

export interface DcfData {
  ticker: string
  grounding: DcfGrounding
  scenarios: ScenarioAssumption[]
  scenario_values: ScenarioResult[]
  monte_carlo: MonteCarloResult
  distributions: Record<AssumptionKey, Distribution>
  reverse_dcf: ReverseDcfResult
  sensitivity: SensitivityMatrix
  verdict: Verdict
  multiples_check: MultiplesCheck
  roic_flag: RoicFlag
  risks: string[]
  key_drivers: string[]
  model: string
  cached: boolean
}
