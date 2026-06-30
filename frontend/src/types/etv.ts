// Expected Tradable Value (ETV) API contract.
// Must mirror backend/services/etv_service.py response schema.

export type EtvHorizon = 'short' | 'medium' | 'long'
export type EtvRiskTolerance = 'conservative' | 'moderate' | 'aggressive'

export interface EtvGrounding {
  ticker: string
  company_name: string
  sector: string | null
  industry: string | null
  business_summary: string | null
  current_price: number
  market_cap: number | null
  enterprise_value: number | null
  shares_out: number | null
  week52_high: number | null
  week52_low: number | null
  avg_volume_10d: number | null
  implied_vol_30d: number | null
  short_pct_float: number | null
  trailing_pe: number | null
  forward_pe: number | null
  ev_ebitda: number | null
  ev_revenue: number | null
  price_to_fcf: number | null
  price_to_book: number | null
  revenue_ttm: number | null
  revenue_growth_yoy: number | null
  gross_margin: number | null
  ebitda: number | null
  ebitda_margin: number | null
  operating_income: number | null
  operating_margin: number | null
  net_income: number | null
  eps_ttm: number | null
  free_cash_flow: number | null
  total_debt: number | null
  net_debt: number | null
  cash: number | null
  capex: number | null
  roic: number | null
  forward_revenue: number | null
  forward_eps: number | null
  long_term_growth: number | null
  analyst_count: number | null
  analyst_recommendation: string | null
  analyst_target_mean: number | null
  analyst_target_high: number | null
  analyst_target_low: number | null
  sma_50: number | null
  sma_200: number | null
  rsi_14: number | null
  as_of: string
}

export interface EtvValueDecomposition {
  fundamental: number | null
  regime_adjustment: number | null
  market_expectations_adjustment: number | null
  optionality: number | null
  behavioral_premium: number | null
}

export interface EtvScenario {
  probability_pct: number | null
  price: number | null
  economic_value: number | null
  optionality_value: number | null
  regime_multiplier: string | null
  behavior_impact: string | null
  value_decomposition: EtvValueDecomposition | null
  conditions: string[]
  rationale: string
  derivation?: string[]
}

export interface EtvRiskRow {
  name: string
  probability_pct: number | null
  magnitude_pct: number | null
  expected_cost_pct: number | null
  trigger: string
}

export interface EtvCatalyst {
  name: string
  timing: string
  direction: 'Positive' | 'Negative' | 'Mixed'
}

export type EtvConfidence = 'High' | 'Medium' | 'Low'

export interface EtvGateAdjustment {
  source: 'lr_fragility' | 'anchored_to_spot'
  delta: number
  reason: string
}

export interface EtvReport {
  company_summary: string
  missing_inputs: string[]

  model_selection: {
    primary_archetype: string
    secondary_archetypes: string[]
    primary_model: string
    primary_model_rationale: string
    supporting_models: string[]
    excluded_models: string[]
    excluded_reason: string
    selection_confidence: EtvConfidence
  }

  economic_value: {
    bear: EtvScenario
    base: EtvScenario
    bull: EtvScenario
    central_estimate: number | null
    low_range: number | null
    high_range: number | null
    key_drivers: string[]
    key_sensitivities: string[]
  }

  optionality: {
    structural_score_out_of_10: number | null
    dominant_advantages: string[]
    low_realisation: number | null
    base_realisation: number | null
    high_realisation: number | null
    probability_weighted: number | null
    strategic_scarcity: 'High' | 'Medium' | 'Low' | 'None'
    pathways: string[]
    decay_risks: string[]
  }

  market_implied: {
    implied_revenue_growth_pct: number | null
    implied_margin_pct: number | null
    implied_growth_duration_years: number | null
    implied_tam_capture_pct: number | null
    expectation_gaps: string[]
    overall_assessment: 'Priced to perfection' | 'Fair' | 'Underappreciated'
  }

  market_behavior: {
    sentiment: 'Euphoric' | 'Positive' | 'Neutral' | 'Negative' | 'Fearful'
    narrative_intensity: 'High' | 'Medium' | 'Low'
    institutional_flow: string
    crowding_risk: 'High' | 'Medium' | 'Low'
    momentum:
      | 'Strong uptrend'
      | 'Weak uptrend'
      | 'Neutral'
      | 'Weak downtrend'
      | 'Strong downtrend'
    options_positioning: string
    behavioral_edge: 'Yes' | 'No' | 'Marginal'
    key_risks: string[]
  }

  regime: {
    primary_regime: string
    secondary_regimes: string[]
    confidence: EtvConfidence
    macro_drivers: string[]
    model_validity: 'valid' | 'partially valid' | 'distorted'
    multiple_bias: 'expansion' | 'neutral' | 'contraction'
    momentum_durability: EtvConfidence
    transition_probability_pct: number | null
    transition_trigger: string
  }

  etv: {
    bear: EtvScenario
    base: EtvScenario
    bull: EtvScenario
    probability_weighted_etv: number | null
    current_price: number | null
    expected_return_pct: number | null
    distribution_skew: 'right-skewed' | 'symmetric' | 'left-skewed'
    primary_driver: string
    weighted_decomposition?: EtvValueDecomposition
    weighted_decomposition_sum?: number
  }

  risk: {
    top_risks: EtvRiskRow[]
    stress_scenario_name: string
    stress_etv: number | null
    stress_return_pct: number | null
    stress_probability_pct: number | null
    mae_low_pct: number | null
    mae_high_pct: number | null
    risk_adjusted_expected_return_pct: number | null
    asymmetry_ratio: number | null
  }

  asymmetry: {
    upside_pct_weighted: number | null
    downside_pct_weighted: number | null
    ratio: number | null
    edge_sources: string[]
    valid: 'Yes' | 'No' | 'Marginal'
    driver: string
  }

  decision: {
    decision: 'TRADE' | 'NO TRADE'
    direction: 'LONG' | 'SHORT' | 'NEUTRAL'
    confidence_pct: number | null
    // LLM's pre-guard thesis confidence (0–90), before deterministic
    // server gate penalties. confidence_pct = thesis_confidence_pct +
    // Σ(gate_adjustments deltas).
    thesis_confidence_pct?: number | null
    gate_adjustments?: EtvGateAdjustment[]
    confidence_deductions: string[]
    horizon: 'Short' | 'Medium' | 'Long'
    horizon_rationale: string
    horizon_catalysts: string[]
  }

  sizing: {
    raw_kelly_pct: number | null
    adjusted_kelly_pct: number | null
    recommended_allocation_pct: number | null
    max_allocation_pct: number | null
    stop_loss_price: number | null
    stop_loss_pct: number | null
    reassessment_trigger: string
    options_structure:
      | 'None'
      | 'Calls'
      | 'Puts'
      | 'Put spread'
      | 'Call spread'
      | 'Straddle'
      | 'Strangle'
    options_rationale: string
  }

  catalysts: EtvCatalyst[]
  failure_conditions: string[]
  core_thesis: string[]
  advisor_challenges: string[]

  validation?: {
    warnings: string[]
    corrections: string[]
    passed: boolean
    probability_check?: EtvProbabilityCheck
  }
}

export interface EtvProbabilityCheck {
  method: 'iv_posterior' | 'llm_only'
  iv_annual: number | null
  horizon_days: number | null
  lr_provided: boolean
  prior_pct: { bear: number; base: number; bull: number } | null
  lr_llm: { bear: number; base: number; bull: number } | null
  lr_clamped: { bear: number; base: number; bull: number } | null
  posterior_pct: { bear: number; base: number; bull: number } | null
  llm_pct: { bear: number; base: number; bull: number } | null
  ratio_llm: number | null
  ratio_prior: number | null
  ratio_posterior: number | null
  decision_under_prior: 'TRADE' | 'NO TRADE' | null
  decision_under_posterior: 'TRADE' | 'NO TRADE' | null
  decision_relies_on_llm_view: boolean
  ratio_gap_llm_vs_posterior: number | null
  decision_fragile: boolean
}

export interface EtvGuardReport {
  passed: boolean
  total_numbers: number
  grounded_count: number
  declared_count: number
  derived_count: number
  passthrough_count: number
  unjustified: Array<{ path: string; value: number | string }>
}

export interface EtvPipelineLogEntry {
  stage: string
  latency_ms: number
  retries: number
  guard?: EtvGuardReport
  extra?: Record<string, unknown>
  reason?: string
}

export interface EtvData {
  ticker: string
  horizon: EtvHorizon
  risk_tolerance: EtvRiskTolerance
  grounding: EtvGrounding
  report: EtvReport
  model: string
  cached: boolean
  cache_age_sec: number
  generated_at: string
  pipeline_enabled?: boolean
  pipeline_log?: EtvPipelineLogEntry[]
}
