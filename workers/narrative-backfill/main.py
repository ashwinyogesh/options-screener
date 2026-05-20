"""Narrative-backfill worker entry point.

Container Apps Job — runs daily after US market close (cron 0 22 * * *
= 22:00 UTC = 18:00 ET, ~2h after the 16:00 ET close to give yfinance
time to publish the day's bars).

What it does:
  1. Queries ``signal_events`` for any doc with ``backfilled_at`` null
     and ``event_date`` in the past.
  2. For each event, fetches T+0/T+5/T+10/T+20 closing prices via
     yfinance for the event ticker AND the benchmark (SPY).
  3. Patches the doc with whatever it can — null fields stay null and
     are retried on the next run.
  4. Marks the doc ``backfilled_at = <now>`` once every T+0..T+20 slot
     (both ticker and SPY) is populated.  Completed docs are excluded
     from future runs.

Idempotent: re-running produces identical results for the same event.

Env contract: see ``config.py``.
"""
from __future__ import annotations

import logging
import sys

from config import load_from_env
from cosmos_client import BackfillCosmosClient
from price_fetcher import ForwardPrices, fetch_forward_prices

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    config = load_from_env()
    _setup_logging(config.log_level)
    logger.info(
        "Starting narrative-backfill — max_events=%d benchmark=%s",
        config.max_events, config.benchmark_ticker,
    )

    cosmos = BackfillCosmosClient(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )

    events = cosmos.fetch_unfilled_events(max_events=config.max_events)
    logger.info("Found %d unfilled events", len(events))

    processed = 0
    completed = 0
    errors = 0

    for event in events:
        ticker = event.get("ticker")
        event_date = event.get("event_date")
        if not ticker or not event_date:
            logger.warning("Skipping malformed event %r", event.get("id"))
            continue

        try:
            ticker_prices = fetch_forward_prices(ticker, event_date)
            spy_prices = fetch_forward_prices(config.benchmark_ticker, event_date)

            is_complete = ticker_prices.is_complete() and spy_prices.is_complete()

            cosmos.update_event_prices(
                event,
                ticker_prices=ticker_prices.as_dict("px"),
                spy_prices=spy_prices.as_dict("spy"),
                mark_complete=is_complete,
            )
            processed += 1
            if is_complete:
                completed += 1
                logger.info(
                    "%s completed (px@signal=%.2f t5=%.2f t10=%.2f t20=%.2f)",
                    event.get("id"),
                    ticker_prices.t0 or 0.0,
                    ticker_prices.t5 or 0.0,
                    ticker_prices.t10 or 0.0,
                    ticker_prices.t20 or 0.0,
                )
            else:
                logger.debug(
                    "%s partial fill ticker=%s spy=%s",
                    event.get("id"),
                    _summarize(ticker_prices),
                    _summarize(spy_prices),
                )
        except Exception:
            logger.exception("Failed to backfill event %s", event.get("id"))
            errors += 1

    logger.info(
        "Backfill complete — processed=%d completed=%d errors=%d total=%d",
        processed, completed, errors, len(events),
    )

    if errors > 0 and processed == 0:
        logger.error("All events failed — exiting non-zero")
        sys.exit(1)


def _summarize(prices: ForwardPrices) -> str:
    return f"t0={prices.t0} t5={prices.t5} t10={prices.t10} t20={prices.t20}"


if __name__ == "__main__":
    main()
