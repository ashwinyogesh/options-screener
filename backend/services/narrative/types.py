"""Typed domain models for narrative intelligence.

These mirror the Cosmos DB schemas defined in docs/NARRATIVE_METHODOLOGY.md.
Routers convert these to Pydantic response models; services produce them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID


# ---------------------------------------------------------------------------
# Phase 3 — ticker_timeline snapshot
# ---------------------------------------------------------------------------
# One document per (ticker, bucket_date) in the Cosmos `ticker_timeline`
# container. The Phase 3 aggregator upserts this every 15 min using
# id = f"{ticker}_{bucket_date}" (deterministic → idempotent upsert).
#
# Field provenance by phase:
#   Phase 3 (aggregator)  — all attention/volume/depth metrics
#   Phase 4 (classifier)  — conviction_* fields (None until Phase 4)
#   Phase 5 (detector)    — lifecycle_stage, stage_confidence (None until Phase 5)
#   Phase 6 (scorer)      — rs_14d, opt_ratio, institutional_13f (None until Phase 6)
# ---------------------------------------------------------------------------

@dataclass
class DailyBucket:
    """Mention count for a single UTC day, used inside TickerTimelineSnapshot."""
    day: str           # ISO date "YYYY-MM-DD"
    count: int         # total signals (posts + comments) mentioning this ticker
    unique_authors: int


@dataclass
class TickerTimelineSnapshot:
    """Aggregated attention metrics for one ticker over a rolling window.

    Written by job-aggregator (Phase 3) to Cosmos `ticker_timeline`.
    Consumed by job-acs-scorer (Phase 6) to compute ACS components A and B.

    §2 attention dimensions covered here:
      2.1 Persistence   → decay_weighted_density_7d / 14d / 30d, daily_buckets
      2.2 Acceleration  → acceleration_7d (ΔV/Δt vs 30-day baseline)
      2.3 Diversity     → unique_authors_14d, gini_14d
      2.4 Depth         → avg_body_len, dd_post_ratio, financial_term_density

    §5 ACS component inputs pre-computed here:
      A (attention persistence) → decay_weighted_density_14d
      B (contributor quality)   → unique_authors_14d, mentions_14d, gini_14d
    """

    # --- Identity ---
    id: str                     # "{ticker}_{bucket_date}", Cosmos document id
    ticker: str                 # partition key — uppercase, e.g. "NVDA"
    bucket_date: str            # ISO date this snapshot covers, e.g. "2026-05-13"
    computed_at: str            # ISO 8601 UTC timestamp of last computation

    # --- Raw volume ---
    mentions_7d: int            # total signal count in rolling 7-day window
    mentions_14d: int           # total signal count in rolling 14-day window
    mentions_30d: int           # total signal count in rolling 30-day window

    # --- §2.1 Persistence — decay-weighted density (λ=0.1, half-life≈7d) ---
    decay_weighted_density_7d: float   # normalized [0,1]
    decay_weighted_density_14d: float  # used for ACS component A
    decay_weighted_density_30d: float  # baseline reference

    # Daily buckets for the 30-day window (sorted ascending by day).
    # Enables re-computing decay with different λ without a re-query.
    daily_buckets: list[DailyBucket] = field(default_factory=list)

    # --- §2.2 Acceleration ---
    # ΔV/Δt: (decay_weighted_density_7d - decay_weighted_density_30d) / 30d_baseline
    # Positive = accelerating, negative = decelerating.
    acceleration_7d: float = 0.0

    # --- §2.3 Contributor diversity ---
    unique_authors_14d: int = 0        # distinct author_hash values in 14d window
    gini_14d: float = 0.0              # Gini coefficient [0,1]; >0.65 = concentration flag

    # --- §2.4 Discussion depth ---
    avg_body_len: float = 0.0          # average body length of signals in 14d window
    dd_post_ratio: float = 0.0         # fraction of signals with DD-flagged flair/terms
    financial_term_density: float = 0.0  # avg fraction of tokens that are financial terms

    # --- §2.5 Composite attention quality (normalized [0, 1]) ---
    # Weighted combination of the four §2 dimensions:
    #   0.35·persistence + 0.25·diversity + 0.25·depth + 0.15·acceleration
    # Normalization functions defined in attention._normalize_for_quality.
    # Useful for ranking and dashboards; ACS components A–D continue to use
    # the raw inputs directly per §5.
    attention_quality: float = 0.0

    # --- Sentiment distribution (from extractor, not conviction classifier) ---
    # Simple polarity ratios from GPT-4o-mini extraction (Phase 2 output).
    # These are NOT the conviction states (Phase 4). Useful for early signal.
    bullish_ratio: float = 0.0         # fraction of signals with sentiment="bullish"
    bearish_ratio: float = 0.0         # fraction of signals with sentiment="bearish"
    avg_confidence: float = 0.0        # mean confidence score across all signals in 14d

    # --- Conviction axes (ADR-0020 / ADR-0021) ---
    # All None until the axis-aware classifier has tagged at least one signal
    # in the 14d window. Marginals drive UI + detector; joint shares drive
    # ACS Component D. See backend/services/narrative/attention.compute_axis_distributions.
    conviction_bull_share: float | None = None
    conviction_researched_share: float | None = None
    conviction_entering_share: float | None = None
    conviction_exiting_share: float | None = None
    conviction_driver_top: str | None = None
    conviction_bull_researched_share: float | None = None
    conviction_bear_researched_share: float | None = None
    conviction_classified_14d: int | None = None

    # --- Phase 5+ fields (None until narrative detector runs) ---
    # lifecycle_stage: int | None = None        # 1..6
    # stage_confidence: float | None = None

    # --- Phase 6+ fields (None until scorer runs, sourced from yfinance/EDGAR) ---
    # rs_14d: float | None = None               # sector-relative strength
    # opt_ratio: float | None = None            # options volume / open interest
    # institutional_13f_change: float | None = None



@dataclass(frozen=True)
class AcsComponents:
    a_attention_persistence: float
    b_contributor_quality: float
    c_narrative_strength: float
    d_thesis_quality: float
    e_market_confirmation: float


@dataclass(frozen=True)
class AcsScore:
    ticker: str
    scored_at: datetime
    acs: float
    acs_ci_lower: float
    acs_ci_upper: float
    components: AcsComponents
    dominant_signal: str
    decay_acs: float
    flags: list[str] = field(default_factory=list)
    # Phase 5 lifecycle fields surfaced for the frontend stage badge.
    # Default to 0 / 0.0 when the detector hasn't run yet for this ticker.
    lifecycle_stage: int = 0           # 0 = unknown, 1..6 = methodology §4
    stage_confidence: float = 0.0
    # ADR-0023 — continuity fields. All optional: scorer back-fills over the
    # first few runs after deploy; pre-ADR-0023 docs return None for slope and
    # 0 / None for streak so the frontend can render "—" cleanly.
    stage_streak_days: int = 0
    first_emerged_at: str | None = None     # ISO date "YYYY-MM-DD"
    acs_slope_14d: float | None = None      # ACS points / day, +ve = rising


@dataclass(frozen=True)
class DailyBucketOut:
    """Single (day, mention_count, unique_authors) tuple for sparkline."""
    day: str           # ISO date
    count: int
    unique_authors: int


@dataclass(frozen=True)
class TickerDetail:
    """Full ticker_timeline projection for the drilldown panel.

    Built from the same Cosmos doc as AcsScore but carries the timeline-shaped
    fields (daily_buckets, tier pcts, conviction ratios) the score itself
    doesn't include.
    """
    ticker: str
    bucket_date: str
    score: AcsScore
    daily_buckets: list[DailyBucketOut] = field(default_factory=list)
    tier1_pct: float = 0.0
    tier2_pct: float = 0.0
    tier3_pct: float = 0.0
    mentions_14d: int = 0
    unique_authors_14d: int = 0
    gini_14d: float = 0.0
    contributor_count_growth_7d: float = 0.0
    # ADR-0020 / ADR-0021 — axis-only conviction view.
    conviction_bull_share: float | None = None
    conviction_researched_share: float | None = None
    conviction_entering_share: float | None = None
    conviction_exiting_share: float | None = None
    conviction_driver_top: str | None = None
    conviction_bull_researched_share: float | None = None
    conviction_bear_researched_share: float | None = None
    conviction_classified_14d: int | None = None


@dataclass(frozen=True)
class NarrativeCluster:
    narrative_id: UUID
    label: str
    associated_tickers: list[str]
    lifecycle_stage: int  # 1..6
    stage_confidence: float
    velocity_14d: float
    cross_sub_count: int
    top_terms: list[str]
    first_seen_utc: datetime
    last_updated_utc: datetime


@dataclass(frozen=True)
class NarrativeAlert:
    ticker: str
    alert_type: str  # e.g. "stage_2_entry", "acs_rising_fast"
    triggered_at: datetime
    payload: dict[str, object]
