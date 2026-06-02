"""Static sector → peer-multiple lookup for the Path-to-Target screen.

We keep this hardcoded (rather than fetched) so the realism bands stay
auditable and don't drift with daily market moves. Numbers are rough
long-run P/E and P/FCF bands by yfinance sector; treat as guard-rails,
not precision.

The Path-to-Target service blends P/E and P/FCF into a single
``low``/``high`` band per sector — non-finance users see "the band of
multiples typical for peers", and we don't ask them to pick a basis.

Methodology: see docs/DD_COACH_METHODOLOGY.md §6 (Path to Target).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeerBand:
    label: str
    low: float
    high: float


# Keys match yfinance ``info["sector"]`` strings.
_SECTOR_BANDS: dict[str, PeerBand] = {
    "Technology": PeerBand("Technology peers", 18.0, 28.0),
    "Communication Services": PeerBand("Communication-services peers", 14.0, 22.0),
    "Consumer Cyclical": PeerBand("Consumer-cyclical peers", 14.0, 22.0),
    "Consumer Defensive": PeerBand("Consumer-staples peers", 18.0, 24.0),
    "Healthcare": PeerBand("Healthcare peers", 16.0, 24.0),
    "Financial Services": PeerBand("Financial-services peers", 10.0, 15.0),
    "Industrials": PeerBand("Industrial peers", 14.0, 20.0),
    "Energy": PeerBand("Energy peers", 8.0, 14.0),
    "Basic Materials": PeerBand("Materials peers", 10.0, 16.0),
    "Real Estate": PeerBand("Real-estate peers", 14.0, 22.0),
    "Utilities": PeerBand("Utility peers", 14.0, 20.0),
}

_GENERIC_FALLBACK = PeerBand("Broad-market peers", 15.0, 22.0)


def peer_band(sector: str | None) -> PeerBand:
    """Return the peer-multiple band for a yfinance sector string.

    Unknown / missing sectors fall back to a broad-market band so the
    Path-to-Target service is always able to produce a Path B / C answer.
    """
    if not sector:
        return _GENERIC_FALLBACK
    return _SECTOR_BANDS.get(sector, _GENERIC_FALLBACK)
