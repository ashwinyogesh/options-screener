"""Read-side service for the Signal Performance tab (ADR-0030 forward log).

Reads stage-transition events from the Cosmos ``signal_events`` container,
hydrated forward in time by the narrative-backfill worker (T+5/T+10/T+20 price
columns for the ticker and the SPY benchmark).

Public API:
    get_signals(...) -> SignalsResponse   — list of events + aggregate stats

The stats are *theory-defensible aggregates*, not a backtest: hit-rate at
T+5/T+10/T+20 is the share of fully-hydrated events whose excess return vs
SPY is positive, and median excess return is the simple median of those
excess returns. Events whose price columns are still null (event_date is too
recent for the backfill to have hydrated) are excluded from the stats
denominator but still returned in the row list so the UI can render them.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

from .cosmos_client import query_signal_events
from .errors import NarrativeUnavailable

logger = logging.getLogger(__name__)


# Forward horizons that the backfill worker fills. Must match
# workers/narrative-backfill/price_fetcher.FORWARD_OFFSETS.
_HORIZONS: tuple[int, ...] = (5, 10, 20)


@dataclass(frozen=True)
class SignalEvent:
    """Single stage-transition row as returned to the API layer."""

    id: str
    ticker: str
    event_date: str            # ISO "YYYY-MM-DD"
    event_ts: str              # ISO 8601 UTC
    prev_stage: int
    new_stage: int
    transition: str            # encoded "{prev}to{new}"
    confidence: float
    breadth_score: float | None
    breadth_delta: float | None
    px_at_signal: float | None
    px_t5: float | None
    px_t10: float | None
    px_t20: float | None
    spy_at_signal: float | None
    spy_t5: float | None
    spy_t10: float | None
    spy_t20: float | None
    backfilled_at: str | None
    excess_t5: float | None
    excess_t10: float | None
    excess_t20: float | None


@dataclass(frozen=True)
class HorizonStats:
    """Aggregate stats at a single forward horizon (T+5, T+10, T+20)."""

    horizon_days: int
    n_complete: int                  # rows with both ticker and SPY prices filled
    hit_rate: float | None           # share of n_complete with excess_return > 0
    median_excess_return: float | None  # median excess return across n_complete


@dataclass(frozen=True)
class SignalsResponse:
    n_total: int                     # rows returned (incl. unhydrated)
    horizons: list[HorizonStats] = field(default_factory=list)
    events: list[SignalEvent] = field(default_factory=list)


def _excess_return(
    px0: float | None, pxN: float | None,
    spy0: float | None, spyN: float | None,
) -> float | None:
    """Excess return = ticker return − SPY return.

    Returns None if any input is missing or px0/spy0 is zero (degenerate).
    """
    if px0 is None or pxN is None or spy0 is None or spyN is None:
        return None
    if px0 == 0 or spy0 == 0:
        return None
    ticker_ret = (pxN - px0) / px0
    spy_ret = (spyN - spy0) / spy0
    return ticker_ret - spy_ret


def _doc_to_event(doc: dict) -> SignalEvent:
    prev_stage = int(doc.get("prev_stage") or 0)
    new_stage = int(doc.get("new_stage") or 0)
    px0 = doc.get("px_at_signal")
    spy0 = doc.get("spy_at_signal")
    return SignalEvent(
        id=str(doc.get("id", "")),
        ticker=str(doc.get("ticker", "")).upper(),
        event_date=str(doc.get("event_date", "")),
        event_ts=str(doc.get("event_ts", "")),
        prev_stage=prev_stage,
        new_stage=new_stage,
        transition=f"{prev_stage}to{new_stage}",
        confidence=float(doc.get("confidence") or 0.0),
        breadth_score=_opt_float(doc.get("breadth_score")),
        breadth_delta=_opt_float(doc.get("breadth_delta")),
        px_at_signal=_opt_float(px0),
        px_t5=_opt_float(doc.get("px_t5")),
        px_t10=_opt_float(doc.get("px_t10")),
        px_t20=_opt_float(doc.get("px_t20")),
        spy_at_signal=_opt_float(spy0),
        spy_t5=_opt_float(doc.get("spy_t5")),
        spy_t10=_opt_float(doc.get("spy_t10")),
        spy_t20=_opt_float(doc.get("spy_t20")),
        backfilled_at=doc.get("backfilled_at"),
        excess_t5=_excess_return(px0, doc.get("px_t5"), spy0, doc.get("spy_t5")),
        excess_t10=_excess_return(px0, doc.get("px_t10"), spy0, doc.get("spy_t10")),
        excess_t20=_excess_return(px0, doc.get("px_t20"), spy0, doc.get("spy_t20")),
    )


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def compute_horizon_stats(events: list[SignalEvent]) -> list[HorizonStats]:
    """Aggregate hit rate and median excess return at each forward horizon."""
    out: list[HorizonStats] = []
    for h in _HORIZONS:
        attr = f"excess_t{h}"
        excess = [getattr(e, attr) for e in events if getattr(e, attr) is not None]
        if not excess:
            out.append(HorizonStats(
                horizon_days=h, n_complete=0,
                hit_rate=None, median_excess_return=None,
            ))
            continue
        hits = sum(1 for x in excess if x > 0)
        out.append(HorizonStats(
            horizon_days=h,
            n_complete=len(excess),
            hit_rate=hits / len(excess),
            median_excess_return=statistics.median(excess),
        ))
    return out


async def get_signals(
    *,
    since: str | None = None,
    min_confidence: float | None = None,
    transition: str | None = None,
    ticker: str | None = None,
    limit: int = 200,
) -> SignalsResponse:
    """Return signal_events rows + aggregate stats over the same set.

    Filters mirror ``query_signal_events``. Stats are computed only over rows
    with fully-hydrated price columns (excess return computable), so they may
    reflect fewer rows than ``n_total``.
    """
    try:
        docs = query_signal_events(
            since=since,
            min_confidence=min_confidence,
            transition=transition,
            ticker=ticker,
            limit=limit,
        )
    except Exception as exc:  # defensive — query_signal_events already swallows
        raise NarrativeUnavailable(f"signal_events query failed: {exc}") from exc

    events = [_doc_to_event(d) for d in docs]
    return SignalsResponse(
        n_total=len(events),
        horizons=compute_horizon_stats(events),
        events=events,
    )
