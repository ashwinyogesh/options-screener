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
5. Writes a pre-computed scoreboard to narrative_cache so /top and /emerging
   use a single point read instead of a 2,800-doc cross-partition scan (Phase B).

Tickers are scored concurrently (_CONCURRENCY = 10) via asyncio + thread-pool,
mirroring workers/screener/runner.py. Idempotent per ticker.
FastAPI reads from narrative_cache (warm) with fallback to ticker_timeline scan.

Env contract:
    KEYVAULT_URI        https://kv-narrative-<suffix>.vault.azure.net/
    COSMOS_ENDPOINT     https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    TICKERS_PER_RUN     max tickers scored per execution (default 500)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from config import load_from_env
from cosmos_client import ScorerCosmosClient
from kv_secrets import fetch_secrets
from market_cap_lookup import get_market_cap
# market_confirmation import removed — Component E was retired (ADR-0034).
from scorer import compute_acs, compute_continuity_fields, detect_alerts
from ic_snapshot import (
    build_snapshot_doc,
    fetch_closing_price,
    compute_forward_return,
    should_fill_return,
    FORWARD_DAYS,
    make_snapshot_id,
)

logger = logging.getLogger(__name__)

# Max tickers scored concurrently — bounded by yfinance and Cosmos throughput.
# Mirrors _CONCURRENCY in workers/screener/runner.py (ADR-0024).
# At 10: 200 tickers ≈ 20 batches × ~4 s = ~80 s versus ~800 s serial.
_CONCURRENCY = 10


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def _score_one(
    doc: dict,
    cosmos: ScorerCosmosClient,
    secrets,  # KvSecrets dataclass; typed loosely to avoid import cycle
    today: str,
    sem: asyncio.Semaphore,
) -> tuple[int, int, dict | None]:
    """Score one ticker_timeline doc. Returns (scored, errors, cache_entry|None)."""
    ticker = doc.get("ticker", "?")
    async with sem:
        try:
            # §5.3 small-cap haircut — cached per run in market_cap_lookup._cache.
            if "market_cap" not in doc:
                doc["market_cap"] = await asyncio.to_thread(get_market_cap, ticker)
            # Component E (market_confirmation) removed — ADR-0034.
            result = compute_acs(doc, secrets.weights)
            # ADR-0023: single-partition history read for continuity fields.
            history = await asyncio.to_thread(
                cosmos.fetch_history,
                ticker,
                doc.get("bucket_date", today),
                30,
            )
            continuity = compute_continuity_fields(
                today_stage=doc.get("lifecycle_stage"),
                today_bucket_date=doc.get("bucket_date", today),
                today_acs=result.acs,
                history=history,
            )
            await asyncio.to_thread(
                cosmos.write_acs,
                doc,
                acs=result.acs,
                acs_ci_lower=result.acs_ci_lower,
                acs_ci_upper=result.acs_ci_upper,
                decay_acs=result.decay_acs,
                components=result.components,
                flags=result.flags,
                dominant_signal=result.dominant_signal,
                stage_streak_days=continuity.stage_streak_days,
                first_emerged_at=continuity.first_emerged_at,
                acs_slope_14d=continuity.acs_slope_14d,
            )
            # Phase 7: alert detection.
            alerts = detect_alerts(
                ticker=ticker,
                today_stage=doc.get("lifecycle_stage"),
                today_acs=result.acs,
                bucket_date=doc.get("bucket_date", today),
                history=history,
            )
            if alerts:
                await asyncio.to_thread(cosmos.write_alerts, alerts)
                logger.info(
                    "%s → %d alert(s): %s",
                    ticker, len(alerts), [a["alert_type"] for a in alerts],
                )

            # IC snapshot: write once per ticker per day with full factor vector.
            # Only fires when no snapshot exists yet — idempotent.
            await _write_ic_snapshot_one(
                ticker=ticker,
                today=today,
                result=result,
                continuity=continuity,
                doc=doc,
                cosmos=cosmos,
            )

            logger.debug(
                "%s → acs=%.1f decay=%.1f stage=%s flags=%s",
                ticker, result.acs, result.decay_acs,
                doc.get("lifecycle_stage", "?"), result.flags,
            )
            cache_entry: dict = {
                "ticker": ticker,
                "acs": round(result.acs, 4),
                "acs_ci_lower": round(result.acs_ci_lower, 4),
                "acs_ci_upper": round(result.acs_ci_upper, 4),
                "decay_acs": round(result.decay_acs, 4),
                # ADR-0028 follow-up: scoreboard rows are read by
                # backend/services/narrative/read_service._doc_to_acs which
                # expects the same shape as ticker_timeline docs. Without
                # these fields the API returns zeroed component pills and
                # zero stage_confidence even though ticker_timeline has the
                # real values.
                "acs_components": result.components,
                "acs_flags": list(result.flags),
                "dominant_signal": result.dominant_signal,
                "acs_scored_at": datetime.now(tz=timezone.utc).isoformat(),
                "stage_confidence": doc.get("stage_confidence"),
                "lifecycle_stage": doc.get("lifecycle_stage"),
                "stage_streak_days": continuity.stage_streak_days,
                "first_emerged_at": continuity.first_emerged_at,
                "acs_slope_14d": (
                    round(continuity.acs_slope_14d, 4)
                    if continuity.acs_slope_14d is not None
                    else None
                ),
                # market_cap is fetched above and lives on the
                # ticker_timeline doc. The frontend Emerging table's default
                # 'sub10b' filter discards rows with market_cap == None, so
                # omitting this here makes the Emerging UI render empty even
                # when the API has rows. Same failure mode the ADR-0028
                # follow-up comment above warns about for the score fields.
                "market_cap": doc.get("market_cap"),
                "bucket_date": doc.get("bucket_date", today),
            }
            return 1, 0, cache_entry
        except Exception:
            logger.exception("Failed to score ticker %s", ticker)
            return 0, 1, None


async def _score_all(
    docs: list[dict],
    cosmos: ScorerCosmosClient,
    secrets,
    today: str,
) -> tuple[int, int]:
    """Score all docs concurrently within _CONCURRENCY, then write narrative cache."""
    sem = asyncio.Semaphore(_CONCURRENCY)
    outcomes = await asyncio.gather(
        *[_score_one(doc, cosmos, secrets, today, sem) for doc in docs],
        return_exceptions=True,
    )
    scored = 0
    errors = 0
    cache_entries: list[dict] = []

    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            logger.error("Unhandled gather exception: %s", outcome)
            errors += 1
        else:
            s, e, entry = outcome
            scored += s
            errors += e
            if entry is not None:
                cache_entries.append(entry)

    # Phase B: write pre-computed scoreboard so /top and /emerging bypass the
    # cross-partition scan on every request (ADR-0028).
    if cache_entries:
        await asyncio.to_thread(cosmos.write_narrative_cache, cache_entries)

    return scored, errors


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

    scored, errors = asyncio.run(_score_all(docs, cosmos, secrets, today))

    logger.info(
        "Scorer complete — scored=%d errors=%d",
        scored, errors,
    )

    # Fill forward returns for IC snapshots that are >= 30 days old.
    _fill_mature_ic_returns(cosmos, today)

    if errors > 0 and scored == 0:
        logger.error("All tickers failed — exiting non-zero")
        sys.exit(1)


# ---------------------------------------------------------------------------
# IC snapshot helpers — called from _score_one and main().
# FROZEN schema: do not change field names or forward_days during 90-day window.
# ---------------------------------------------------------------------------

async def _write_ic_snapshot_one(
    ticker: str,
    today: str,
    result,  # AcsResult
    continuity,  # ContinuityFields
    doc: dict,
    cosmos: ScorerCosmosClient,
) -> None:
    """Write one IC snapshot with full factor vector. Idempotent per ticker per day."""
    import datetime as _dt
    snap_id = make_snapshot_id(ticker, today)
    existing = await asyncio.to_thread(cosmos.get_ic_snapshot, ticker, today)
    if existing is not None:
        return
    today_date = _dt.date.fromisoformat(today)
    px = await asyncio.to_thread(fetch_closing_price, ticker, today_date)
    snap = build_snapshot_doc(
        ticker=ticker,
        snapshot_date=today,
        acs=result.acs,
        px_at_snapshot=px,
        factors={
            # ACS variants
            "acs_raw": result.acs_raw,
            "acs_multiplier": result.acs_multiplier,
            "decay_acs": result.decay_acs,
            "acs_ci_lower": result.acs_ci_lower,
            "acs_ci_upper": result.acs_ci_upper,
            # Individual components
            "comp_a": result.components.get("A"),
            "comp_b": result.components.get("B"),
            "comp_c": result.components.get("C"),
            "comp_d": result.components.get("D"),
            # Raw inputs (for per-factor IC analysis)
            "dwd_14d": doc.get("decay_weighted_density_14d"),
            "unique_authors_14d": doc.get("unique_authors_14d"),
            "mentions_14d": doc.get("mentions_14d"),
            "gini_14d": doc.get("gini_14d"),
            "lifecycle_stage": doc.get("lifecycle_stage"),
            "stage_confidence": doc.get("stage_confidence"),
            "s_br": doc.get("conviction_bull_researched_share"),
            "s_Br": doc.get("conviction_bear_researched_share"),
            "market_cap": doc.get("market_cap"),
            # Continuity / momentum
            "stage_streak_days": continuity.stage_streak_days,
            "acs_slope_14d": continuity.acs_slope_14d,
            # Metadata
            "dominant_signal": result.dominant_signal,
            "flags": list(result.flags),
        },
    )
    await asyncio.to_thread(cosmos.upsert_ic_snapshot, snap)
    logger.debug("IC snapshot written: %s / %s", ticker, today)


def _fill_mature_ic_returns(cosmos: ScorerCosmosClient, today: str) -> None:
    """For snapshots >= FORWARD_DAYS old, fetch return and mark complete."""
    import datetime as _dt
    today_date = _dt.date.fromisoformat(today)
    pending = cosmos.fetch_pending_ic_snapshots(limit=300)
    filled = 0
    for snap in pending:
        if not should_fill_return(snap, today_date):
            continue
        ticker = snap.get("ticker", "")
        snap_date_str = snap.get("snapshot_date", "")
        px_entry = snap.get("px_at_snapshot")
        if not ticker or not snap_date_str or not px_entry:
            continue
        try:
            exit_date = _dt.date.fromisoformat(snap_date_str) + _dt.timedelta(days=FORWARD_DAYS)
        except ValueError:
            continue
        px_exit = fetch_closing_price(ticker, exit_date)
        if px_exit is None:
            continue
        snap["forward_return_pct"] = round(compute_forward_return(px_entry, px_exit), 4)
        snap["is_complete"] = True
        snap["filled_at"] = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
        cosmos.upsert_ic_snapshot(snap)
        filled += 1
    if filled:
        logger.info("IC snapshots return-filled: %d", filled)


if __name__ == "__main__":
    main()

