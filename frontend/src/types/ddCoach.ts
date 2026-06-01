// Types mirror backend/services/dd_coach/* dataclasses + models.

export type ValuationMethod = 'multiple_based' | 'maturity_discount' | 'optionality'
export type EntryStatus = 'draft' | 'completed'
export type UserCall = 'cheap' | 'fair' | 'expensive_worth_it' | 'cannot_value'
export type StomachAnswer = 'yes' | 'unsure' | 'no'

// ---------- Data card ----------

export interface YearlyMetric {
  year: number
  value: number | null
}

export interface HardRailFlags {
  balance_sheet_red: boolean
  reasons: string[]
}

export interface GrowthLens {
  gross_margin_3yr: YearlyMetric[]
  cash_runway_years: number | null
  share_dilution_pct_3yr: number | null
  summary: string
}

export interface DataCard {
  ticker: string
  company_name: string | null
  sector: string | null
  industry: string | null
  spot_price: number | null
  market_cap: number | null
  revenue_3yr: YearlyMetric[]
  fcf_3yr: YearlyMetric[]
  cash: number | null
  debt: number | null
  net_cash_position: number | null
  price_to_sales_ttm: number | null
  price_to_earnings_ttm: number | null
  flags: HardRailFlags
  growth_lens: GrowthLens | null
}

// ---------- Filings ----------

export interface FilingLinks {
  ticker: string
  cik: string
  all_filings: string
  latest_10k: string
  latest_10q: string
  latest_8k: string
  proxy_def14a: string
  form4_insider: string
}

// ---------- Valuation ----------

export interface ValuationRange {
  bear: number | null
  base: number | null
  bull: number | null
  spot: number | null
}

export interface ValuationOutput {
  method: ValuationMethod
  range: ValuationRange
  inputs_used: Record<string, unknown>
  rationale: string
}

export interface MultipleBasedInputs {
  forward_eps: number
  target_pe_low: number
  target_pe_mid: number
  target_pe_high: number
  spot_price?: number | null
}

export interface MaturityDiscountInputs {
  revenue_bear: number
  revenue_base: number
  revenue_bull: number
  mature_multiple: number
  shares_outstanding_today: number
  spot_price?: number | null
  years_to_maturity?: number
  dilution_pct?: number
  discount_rate?: number
}

export interface ValuationRequest {
  method: ValuationMethod
  spot_price?: number | null
  multiple_based?: MultipleBasedInputs
  maturity_discount?: MaturityDiscountInputs
}

// ---------- Entry document ----------

export interface Answers {
  q1_business?: string | null
  q2_revenue_model?: string | null
  q3_upside?: string | null
  q4_risks?: string | null
  q3_market?: string | null
  q3_moat?: string | null
  q3_why_now?: string | null
}

export interface Valuation {
  method?: ValuationMethod | null
  inputs?: Record<string, unknown>
  result?: ValuationRange | null
  user_call?: UserCall | null
  reasoning?: string | null
}

export interface Sizing {
  planned_dollars?: number | null
  stomach_answer?: StomachAnswer | null
  final_dollars?: number | null
}

export interface DDEntry {
  id: string
  ticker: string
  user_id: string
  status: EntryStatus
  created_at: string
  updated_at: string
  completed_at: string | null
  data_card_snapshot: Record<string, unknown>
  answers: Answers
  valuation: Valuation
  sizing: Sizing
}

export interface PatchEntryInput {
  data_card_snapshot?: Record<string, unknown>
  answers?: Answers
  valuation?: Valuation
  sizing?: Sizing
}

export interface DDCoachError {
  detail: string
  unavailable: boolean
}
