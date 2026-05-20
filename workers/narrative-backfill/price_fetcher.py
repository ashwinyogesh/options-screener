"""Forward price fetching for the narrative-backfill worker.

Given a list of signal events with their ``event_date``, fetch the
closing price on the event date and at T+5/T+10/T+20 *trading* days for
both the event ticker and a benchmark (SPY by default).

Wraps yfinance and is intentionally tolerant: any unfetchable price
returns ``None`` and the corresponding column stays null on the doc, so
the backfill can retry on a subsequent run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


# Trading-day offsets to back-fill.  T+0 is the event-date close itself.
FORWARD_OFFSETS: tuple[int, ...] = (0, 5, 10, 20)

# Calendar buffer added to the fetch window so 20 trading days always fit
# (worst case ~28 calendar days for 20 trading days including holidays).
_CALENDAR_BUFFER_DAYS: int = 35


@dataclass(frozen=True)
class ForwardPrices:
    """Closes at the event date and T+5 / T+10 / T+20 trading days.

    Any field can be ``None`` when the trading day hasn't occurred yet or
    yfinance returned no data for that bar.
    """
    t0: float | None
    t5: float | None
    t10: float | None
    t20: float | None

    def as_dict(self, prefix: str) -> dict[str, float | None]:
        return {
            f"{prefix}_at_signal": self.t0,
            f"{prefix}_t5": self.t5,
            f"{prefix}_t10": self.t10,
            f"{prefix}_t20": self.t20,
        }

    def is_complete(self) -> bool:
        return all(v is not None for v in (self.t0, self.t5, self.t10, self.t20))


def fetch_forward_prices(
    ticker: str,
    event_date: str,
    *,
    today: date | None = None,
) -> ForwardPrices:
    """Return closing prices for *ticker* at T+0 / T+5 / T+10 / T+20.

    ``event_date`` is ISO ``YYYY-MM-DD``.  ``today`` defaults to UTC today
    and is parameterizable for tests.  Trading days are derived from the
    actual close series returned by yfinance — no holiday calendar logic
    lives in this module.
    """
    return _fetch(ticker, event_date, today=today or _utc_today())


def _utc_today() -> date:
    return datetime.utcnow().date()


def _fetch(ticker: str, event_date: str, *, today: date) -> ForwardPrices:
    try:
        import yfinance as yf  # noqa: PLC0415 — heavy import deferred
    except ImportError:
        logger.warning("yfinance not installed — backfill cannot run")
        return ForwardPrices(None, None, None, None)

    try:
        start = datetime.strptime(event_date, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("Invalid event_date %r — skipping", event_date)
        return ForwardPrices(None, None, None, None)

    # Cap the end at today (yfinance returns nothing for future dates) but
    # always request the full calendar buffer so partial back-fills work.
    end_target = start + timedelta(days=_CALENDAR_BUFFER_DAYS)
    end = min(end_target, today) + timedelta(days=1)  # yfinance end is exclusive

    if end <= start:
        # Event hasn't happened yet — nothing to fetch.
        return ForwardPrices(None, None, None, None)

    try:
        hist = yf.download(  # type: ignore[attr-defined]
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        logger.exception("yfinance download failed for %s @ %s", ticker, event_date)
        return ForwardPrices(None, None, None, None)

    if hist is None or hist.empty:
        return ForwardPrices(None, None, None, None)

    try:
        closes = hist["Close"].dropna()
    except KeyError:
        return ForwardPrices(None, None, None, None)

    # yfinance occasionally returns a MultiIndex on ``Close`` when a single
    # ticker is fetched alongside groupings.  Squeeze to a 1D series.
    if hasattr(closes, "squeeze"):
        try:
            closes = closes.squeeze("columns") if closes.ndim > 1 else closes
        except (TypeError, ValueError):
            pass

    closes_list = [float(v) for v in closes.tolist()]
    return _pick_offsets(closes_list)


def _pick_offsets(closes: list[float]) -> ForwardPrices:
    """Pull T+0/5/10/20 closes from a contiguous trading-day list."""
    def at(offset: int) -> float | None:
        return closes[offset] if 0 <= offset < len(closes) else None

    return ForwardPrices(
        t0=at(0),
        t5=at(5),
        t10=at(10),
        t20=at(20),
    )
