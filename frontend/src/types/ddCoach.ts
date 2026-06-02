// Types mirror backend/services/dd_coach/* dataclasses + models.

export type ValuationMethod = 'multiple_based' | 'maturity_discount' | 'optionality'
export type EntryStatus = 'draft' | 'completed'
export type UserCall = 'cheap' | 'fair' | 'expensive_worth_it' | 'cannot_value'
export type StomachAnswer = 'yes' | 'unsure' | 'no'

export type FlagAcknowledgment = 'accounted' | 'changes_view' | 'explained'
export type InsiderActivity =
  | 'heavy_buy'
  | 'light_buy'
  | 'quiet'
  | 'light_sell'
  | 'heavy_sell'
  | 'unknown'
export type CompStructure = 'revenue' | 'profit' | 'stock' | 'salary' | 'unknown'

export type Realism = 'easy' | 'plausible' | 'stretch' | 'unrealistic'
export type CashBasis = 'earnings' | 'fcf'

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
  revenue_ttm: number | null
  fcf_ttm: number | null
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

export interface FlagResponse {
  acknowledgment: FlagAcknowledgment
  note?: string | null
}

export interface LeadershipCheck {
  who?: string | null
  insider_activity?: InsiderActivity | null
  comp_structure?: CompStructure | null
  concerns?: string | null
}

export interface Answers {
  q1_business?: string | null
  q2_revenue_model?: string | null
  q3_upside?: string | null
  q4_risks?: string | null
  q3_market?: string | null
  q3_moat?: string | null
  q3_why_now?: string | null
  // V2 additions
  q1_flag_response?: FlagResponse | null
  q5_leadership?: LeadershipCheck | null
  q9_bear_case?: string | null
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
  // V2 plan-pre-commit
  portfolio_pct_estimate?: number | null
  sell_target?: number | null
  add_more_price?: number | null
  bail_out_trigger?: string | null
  commitment_acknowledged?: boolean
}

// ---------- Path to Target (Screen 6) ----------

export interface PathResult {
  applicable: boolean
  realism: Realism | null
  required_growth_pct: number | null
  required_multiple: number | null
  note: string
}

export interface PathToTarget {
  ticker: string
  spot: number | null
  target: number
  target_return_pct: number | null
  cash_basis: CashBasis | null
  cash_per_share: number | null
  current_multiple: number | null
  historical_growth_pct: number | null
  peer_label: string
  peer_multiple_low: number
  peer_multiple_high: number
  path_a_growth_only: PathResult
  path_b_multiple_only: PathResult
  path_c_mixed: PathResult
  notes: string[]
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

// ---------- Filings intelligence (V3) ----------

export type InsightType =
  | 'business_summary'
  | 'risk_diff'
  | 'mda_summary'
  | 'leadership'
  | 'bear_scaffold'

export interface IntelSource {
  form: string
  accession: string
  filing_date: string
  primary_doc_url: string
}

export interface IntelResult {
  ticker: string
  insight_type: InsightType
  cache_key: string
  sources: IntelSource[]
  content: Record<string, unknown>
  generated_at: string
  cached: boolean
}

// Per-insight content shapes (matches backend JSON schemas).

export interface BusinessSummaryContent {
  summary: string
  primary_products: string[]
  main_customers: string
  moat_hypothesis: string
  segments: string[]
}

export interface RiskDiffNewRisk {
  title: string
  summary: string
  quote: string
  why_it_matters: string
  severity: 'low' | 'medium' | 'high'
  severity_rationale: string
}

export interface RiskDiffExpandedRisk {
  title: string
  what_changed: string
  quote: string
  why_it_matters: string
  severity: 'low' | 'medium' | 'high'
  severity_rationale: string
}

export interface RiskDiffContent {
  new_risks: RiskDiffNewRisk[]
  expanded_risks: RiskDiffExpandedRisk[]
  overall_tone: 'materially worse' | 'modestly worse' | 'unchanged' | 'modestly better'
  ongoing_risks: RiskDiffNewRisk[]
}

export interface MdaSummaryContent {
  revenue_bridge: string
  margin_drivers: string
  liquidity: string
  forward_tone: 'optimistic' | 'cautious' | 'neutral' | 'guarded'
  highlights: string[]
}

export interface LeadershipContent {
  ceo_name: string
  ceo_tenure_note: string
  comp_alignment:
    | 'heavily stock-linked'
    | 'performance-linked'
    | 'mixed'
    | 'salary-heavy'
    | 'unclear'
  comp_summary: string
  insider_activity_note: string
  concerns: string[]
}

export interface BearScaffoldScenario {
  title: string
  narrative: string
  probability_range_pct: string
  metric_to_watch: string
}

export interface BearScaffoldContent {
  scenarios: BearScaffoldScenario[]
}
