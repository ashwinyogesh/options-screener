"""Attention aggregator worker entry point (Phase 3).

Container Apps Job — runs on a 15-minute cron schedule.

What it does:
1. Queries Cosmos `signals` container for all tickers active in the last 30 days.
2. For each ticker, fetches all signals in the rolling 30-day window.
3. Calls build_snapshot() (pure attention functions, §2 of NARRATIVE_METHODOLOGY.md).
4. Upserts the resulting TickerTimelineSnapshot into `ticker_timeline`.

Idempotent: upsert uses id = f"{ticker}_{bucket_date}" — re-running the job
for the same day overwrites with refreshed metrics.

Env contract:
    COSMOS_ENDPOINT        https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB              narrative  (default)
    LOG_LEVEL              INFO / DEBUG  (default INFO)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from attention import build_snapshot
from config import load_from_env
from cosmos_reader import CosmosReader
from cosmos_writer import CosmosTimelineWriter

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
    logger.info("Starting narrative aggregator (Phase 3)")

    reader = CosmosReader(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )
    writer = CosmosTimelineWriter(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )

    bucket_date = datetime.now(timezone.utc).date()

    tickers = reader.distinct_tickers_last_30d(bucket_date)
    logger.info("Found %d active tickers in last 30 days", len(tickers))

    written = 0
    errors = 0
    for ticker in tickers:
        try:
            signals = reader.signals_for_ticker(ticker, bucket_date)
            snapshot = build_snapshot(ticker, signals, bucket_date)
            writer.upsert(snapshot)
            written += 1
            logger.debug(
                "  %s → mentions_14d=%d dwd_14d=%.3f accel=%.3f gini=%.3f",
                ticker,
                snapshot.mentions_14d,
                snapshot.decay_weighted_density_14d,
                snapshot.acceleration_7d,
                snapshot.gini_14d,
            )
        except Exception:
            logger.exception("Failed to aggregate ticker %s", ticker)
            errors += 1

    logger.info(
        "Done. tickers=%d written=%d errors=%d bucket=%s",
        len(tickers),
        written,
        errors,
        bucket_date.isoformat(),
    )


if __name__ == "__main__":
    main()
