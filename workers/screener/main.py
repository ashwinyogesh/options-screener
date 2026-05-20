"""Screener precomputation worker (ADR-0024).

Container Apps Job — runs on a 15-minute cron schedule.

What it does:
1. Reads the strategy from the STRATEGY env var (csp | cc | ditm).
2. Checks Cosmos to see if existing docs are still fresh (market-aware
   staleness threshold). Exits early if data is recent enough.
3. Iterates over the full MOMENTUM_UNIVERSE, calls process_symbol for each
   ticker, and upserts the result (or error) into the strategy-specific
   Cosmos container (screener_csp / screener_cc / screener_ditm).

Env contract:
    COSMOS_ENDPOINT              https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB                    narrative  (default)
    STRATEGY                     csp | cc | ditm
    LOG_LEVEL                    INFO / DEBUG  (default INFO)
    MIN_REFRESH_SECONDS_MARKET   seconds between refreshes during US market hours
                                 (default 900 = 15 min)
    MIN_REFRESH_SECONDS_OFF      seconds between refreshes outside market hours
                                 (default 14400 = 4 h)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from config import load_from_env
from cosmos_client import ScreenerCosmosClient
from market_hours import is_market_open

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
    logger.info("Starting screener precomputation worker — strategy=%s", config.strategy)

    cosmos = ScreenerCosmosClient(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
        strategy=config.strategy,
    )

    now = datetime.now(tz=timezone.utc)
    threshold = _staleness_threshold(now, config)

    if cosmos.is_fresh(threshold_seconds=threshold):
        logger.info(
            "Precomputed docs are fresh (threshold=%ds) — skipping scan", threshold
        )
        return

    logger.info("Data is stale — running full universe scan for strategy=%s", config.strategy)

    from runner import run_strategy  # noqa: PLC0415 — deferred to avoid importing backend at module level
    results, errors = run_strategy(config.strategy)

    scored = 0
    error_count = 0
    for ticker, result in results.items():
        try:
            # For DITM, macro context is embedded in result["macro"]; lift it
            # to top-level doc fields so get_ditm_results() can read it.
            macro_fields = result.get("macro") or None if config.strategy == "ditm" else None
            cosmos.upsert_result(ticker=ticker, result=result, error=None, macro_fields=macro_fields)
            scored += 1
        except Exception:
            logger.exception("Failed to write result for ticker %s", ticker)
            error_count += 1

    for ticker, reason in errors.items():
        try:
            cosmos.upsert_result(ticker=ticker, result=None, error=reason)
        except Exception:
            logger.exception("Failed to write error doc for ticker %s", ticker)

    logger.info(
        "Screener worker complete — strategy=%s scored=%d errors=%d write_errors=%d",
        config.strategy,
        scored,
        len(errors),
        error_count,
    )

    if error_count > 0 and scored == 0:
        logger.error("All writes failed — exiting non-zero")
        sys.exit(1)


def _staleness_threshold(now: datetime, config: "ScreenerConfig") -> int:  # noqa: F821
    """Return the freshness threshold in seconds based on market hours."""
    from market_hours import is_market_open
    if is_market_open(now):
        return config.min_refresh_seconds_market
    return config.min_refresh_seconds_off


if __name__ == "__main__":
    main()
