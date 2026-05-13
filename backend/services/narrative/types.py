"""Typed domain models for narrative intelligence.

These mirror the Postgres schemas defined in docs/NARRATIVE_METHODOLOGY.md.
Routers convert these to Pydantic response models; services produce them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


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
