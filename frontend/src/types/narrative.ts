/**
 * Narrative intelligence types — mirrors backend/services/narrative/types.py
 * and backend/routers/narrative.py response shapes.
 *
 * See docs/NARRATIVE_METHODOLOGY.md §5 for ACS component definitions.
 */

export interface AcsComponents {
  a_attention_persistence: number  // 0..25
  b_contributor_quality: number    // 0..20
  c_narrative_strength: number     // 0..20
  d_thesis_quality: number         // 0..20
  e_market_confirmation: number    // 0..15
}

export interface AcsScore {
  ticker: string
  scored_at: string  // ISO 8601
  acs: number        // 0..100
  acs_ci_lower: number
  acs_ci_upper: number
  components: AcsComponents
  dominant_signal: string
  decay_acs: number
  flags: string[]
}

export type LifecycleStage = 1 | 2 | 3 | 4 | 5 | 6

export interface NarrativeCluster {
  narrative_id: string  // UUID
  label: string
  associated_tickers: string[]
  lifecycle_stage: LifecycleStage
  stage_confidence: number  // 0..1
  velocity_14d: number
  cross_sub_count: number
  top_terms: string[]
  first_seen_utc: string
  last_updated_utc: string
}

export interface NarrativeAlert {
  ticker: string
  alert_type: string
  triggered_at: string
  payload: Record<string, unknown>
}

export interface NarrativeError {
  detail: string
  /** True when the platform isn't yet provisioned (Phase 0 → 503). */
  unavailable: boolean
}
