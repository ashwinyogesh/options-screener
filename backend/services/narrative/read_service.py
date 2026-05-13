"""Read-side service for the narrative tab.

Phase 0: every method raises NarrativeUnavailable. Phases 1–6 fill in the
implementations. The router contract is finalized here so the frontend can
be built against stable shapes.
"""
from __future__ import annotations

from uuid import UUID

from .errors import NarrativeUnavailable
from .types import AcsScore, NarrativeAlert, NarrativeCluster


async def get_acs_for_ticker(ticker: str) -> AcsScore:
    """Latest ACS row for a ticker. Redis-first in Phase 6, Postgres fallback."""
    raise NarrativeUnavailable("ACS pipeline not yet provisioned (Phase 6)")


async def get_top_tickers(limit: int = 100) -> list[AcsScore]:
    """Top-N tickers by current ACS. Backed by Redis sorted set in Phase 6."""
    raise NarrativeUnavailable("ACS pipeline not yet provisioned (Phase 6)")


async def get_emerging_tickers(limit: int = 50) -> list[AcsScore]:
    """Stage 1–3 tickers with rising ACS over the last 7 days."""
    raise NarrativeUnavailable("ACS pipeline not yet provisioned (Phase 6)")


async def get_narrative(narrative_id: UUID) -> NarrativeCluster:
    """Cluster detail by ID. Populated by job-narrative-detector in Phase 5."""
    raise NarrativeUnavailable("Narrative detection not yet provisioned (Phase 5)")


async def get_alerts(limit: int = 50) -> list[NarrativeAlert]:
    """pg_cron-populated alerts; 60s polling cadence from the frontend."""
    raise NarrativeUnavailable("Alert pipeline not yet provisioned (Phase 6)")
