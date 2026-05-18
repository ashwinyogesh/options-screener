"""ACS scorer entry point (Phase 6).

Container Apps Job — runs on a 15-minute cron schedule.

What it does:
1. Reads today's ticker_timeline documents from Cosmos.
2. For each doc, fetches market-confirmation signals (RS_14d, opt_ratio,
   institutional_13f) from yfinance, then computes ACS components A–E per §5
   of NARRATIVE_METHODOLOGY.md using component max weights from Key Vault
   secret `acs-component-weights` (falls back to design defaults if absent).
3. Applies Gini, deceleration, and late-stage haircuts.
4. Writes acs, acs_ci_lower, acs_ci_upper, decay_acs, acs_components, acs_flags,
   and acs_scored_at back onto the same ticker_timeline document.

Idempotent: re-running produces the same result for the same input doc.
FastAPI backend reads directly from Cosmos ticker_timeline (no Redis, Phase 6).

Env contract:
    KEYVAULT_URI        https://kv-narrative-<suffix>.vault.azure.net/
    COSMOS_ENDPOINT     https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    TICKERS_PER_RUN     max tickers scored per execution (default 500)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from config import load_from_env
from cosmos_client import ScorerCosmosClient
from kv_secrets import fetch_secrets
from market_cap_lookup import get_market_cap
from market_confirmation import get_market_confirmation
from scorer import compute_acs, compute_continuity_fields

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
    logger.info("Starting ACS scorer (Phase 6)")

    secrets = fetch_secrets(config.keyvault_uri)
    cosmos = ScorerCosmosClient(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    docs = cosmos.fetch_today_docs(today, limit=config.tickers_per_run)
    logger.info("Scoring %d ticker_timeline docs for %s", len(docs), today)

    scored = 0
    errors = 0

    for doc in docs:
        ticker = doc.get("ticker", "?")
        try:
            # §5.3 small-cap haircut requires market_cap; fetch once per ticker
            # (cached per run). Failures are non-fatal — the haircut is simply
            # skipped for tickers where yfinance is unreachable.
            if "market_cap" not in doc:
                doc["market_cap"] = get_market_cap(ticker)
            # §5.1 Component E: fetch market-confirmation signals (cached per
            # run; non-fatal — any sub-signal that fails stays at 0.0).
            mc = get_market_confirmation(ticker)
            doc["rs_14d_norm"] = mc.rs_14d_norm
            doc["opt_ratio_norm"] = mc.opt_ratio_norm
            doc["institutional_13f_norm"] = mc.institutional_norm
            result = compute_acs(doc, secrets.weights)
            # ADR-0023: continuity fields are derived from today's freshly
            # scored ACS + the prior ~30 daily docs (single-partition read).
            history = cosmos.fetch_history(
                ticker=ticker,
                bucket_date=doc.get("bucket_date", today),
                days=30,
            )
            continuity = compute_continuity_fields(
                today_stage=doc.get("lifecycle_stage"),
                today_bucket_date=doc.get("bucket_date", today),
                today_acs=result.acs,
                history=history,
            )
            cosmos.write_acs(
                doc,
                acs=result.acs,
                acs_ci_lower=result.acs_ci_lower,
                acs_ci_upper=result.acs_ci_upper,
                decay_acs=result.decay_acs,
                components=result.components,
                flags=result.flags,
                stage_streak_days=continuity.stage_streak_days,
                first_emerged_at=continuity.first_emerged_at,
                acs_slope_14d=continuity.acs_slope_14d,
            )
            scored += 1
            logger.debug(
                "%s → acs=%.1f decay=%.1f stage=%s flags=%s",
                ticker, result.acs, result.decay_acs,
                doc.get("lifecycle_stage", "?"), result.flags,
            )
        except Exception:
            logger.exception("Failed to score ticker %s", ticker)
            errors += 1

    logger.info(
        "Scorer complete — scored=%d errors=%d",
        scored, errors,
    )

    if errors > 0 and scored == 0:
        logger.error("All tickers failed — exiting non-zero")
        sys.exit(1)


if __name__ == "__main__":
    main()

