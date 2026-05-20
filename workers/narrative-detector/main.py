"""Narrative lifecycle detector entry point (Phase 5).

Container Apps Job — runs on an hourly cron schedule.

What it does:
1. Queries `signals` for all tickers with embedded + classified signals in the
   72-hour look-back window.
2. For each ticker, loads the embedding vectors into memory and runs HDBSCAN
   (cosine metric, min_cluster_size=3) per ADR-0017.
3. Merges cluster centroids with cosine similarity > 0.82 into a single thread.
4. Applies pure signal-side lifecycle rules (§4 of NARRATIVE_METHODOLOGY.md) to
   assign lifecycle_stage (1–6) and stage_confidence to the ticker's
   ticker_timeline document for today's bucket.

Signals with null embedding are excluded from clustering (classifier soft-failed
their embedding call; they will be embedded on the next classifier run).

Idempotent: upserts ticker_timeline with the latest stage; re-running produces
the same result for the same input data.

Env contract:
    KEYVAULT_URI        https://kv-narrative-<suffix>.vault.azure.net/
    COSMOS_ENDPOINT     https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    WINDOW_HOURS        embedding look-back window in hours (default 72)
    MIN_CLUSTER_SIZE    HDBSCAN min_cluster_size (default 3)
    MERGE_THRESHOLD     cosine similarity threshold for cluster merging (default 0.82)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from config import load_from_env
from cosmos_client import DetectorCosmosClient
from detector import ClusterResult, assign_stage, cluster
from smoothing import LifecycleState

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
        "Starting narrative-detector (Phase 5) window=%dh min_cluster=%d merge_threshold=%.2f",
        config.window_hours, config.min_cluster_size, config.merge_threshold,
    )

    cosmos = DetectorCosmosClient(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    tickers = cosmos.fetch_active_tickers(config.window_hours)
    logger.info("Active tickers with embeddings: %d", len(tickers))

    processed = 0
    errors = 0

    for ticker in tickers:
        try:
            signals = cosmos.fetch_signals_for_ticker(ticker, config.window_hours)
            if not signals:
                logger.debug("%s: no signals with embeddings in window — skipping", ticker)
                continue

            embeddings = [doc["embedding"] for doc in signals]
            result: ClusterResult = cluster(
                embeddings,
                min_cluster_size=config.min_cluster_size,
                merge_threshold=config.merge_threshold,
                min_intra_cluster_similarity=config.min_intra_cluster_similarity,
            )

            timeline = cosmos.fetch_timeline_doc(ticker, today) or {}

            # Carry hysteresis + smoothing state forward from the previous run
            # (today's earlier run, falling back to yesterday's bucket).  ADR-0029.
            prev_stage, prior_doc = cosmos.fetch_prior_lifecycle(ticker, today)
            prior_state = LifecycleState.from_doc(prior_doc)

            stage, confidence, new_state = assign_stage(
                timeline, result, prior_state=prior_state, prev_stage=prev_stage,
            )

            if stage == 0:
                logger.debug("%s: stage=0 (insufficient data) — skipping write", ticker)
                continue

            cosmos.write_lifecycle(
                ticker, today, stage, confidence,
                lifecycle_state=new_state.to_dict(),
                n_embedded=result.n_embedded,
                dominant_fraction=result.dominant_fraction,
            )
            processed += 1
            logger.info(
                "%s → stage=%d (prev=%d pending=%d) confidence=%.2f "
                "(n_signals=%d n_embedded=%d n_clusters=%d dom_frac=%.2f)",
                ticker, stage, prev_stage, new_state.pending_stage,
                confidence, len(signals), result.n_embedded,
                result.n_clusters, result.dominant_fraction,
            )

        except Exception:
            logger.exception("Failed to process ticker %s", ticker)
            errors += 1

    logger.info(
        "Detector complete — processed=%d errors=%d total_tickers=%d",
        processed, errors, len(tickers),
    )

    if errors > 0 and processed == 0:
        logger.error("All tickers failed — exiting non-zero")
        sys.exit(1)


if __name__ == "__main__":
    main()

