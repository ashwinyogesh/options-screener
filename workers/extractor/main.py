"""Extractor worker entry point (Phase 2).

Container Apps Job — runs on a schedule (e.g. every 5 minutes), consumes up
to `max_events_per_run` messages from Event Hubs `reddit-raw-events`, extracts
ticker + sentiment signals via GPT-4o-mini, and writes to Cosmos DB `signals`.

Failure semantics:
- Per-event extraction failures are logged and skipped.
- Per-signal Cosmos write failures are logged; the event is not re-queued
  (idempotent: re-running the job will not re-process already-checkpointed EH
  offsets because the consumer group tracks its position).
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time

from azure.eventhub import EventHubConsumerClient
from azure.identity import DefaultAzureCredential

from config import ExtractorConfig, load_from_env
from cosmos_writer import CosmosWriter
from extractor import Extractor
from kv_secrets import fetch_secrets

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
    logger.info("Starting narrative extractor worker (Phase 2)")

    secrets = fetch_secrets(config.keyvault_uri)
    extractor = Extractor(
        api_key=secrets.openai_api_key,
        endpoint=secrets.openai_endpoint,
        deployment=secrets.openai_deployment,
        max_tokens=config.openai_max_tokens,
    )
    writer = CosmosWriter(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )

    credential = DefaultAzureCredential()
    eh_client = EventHubConsumerClient(
        fully_qualified_namespace=config.event_hub_namespace,
        eventhub_name=config.raw_events_hub,
        consumer_group="$Default",
        credential=credential,
    )

    events_processed = 0
    signals_written = 0
    collected: list[dict] = []

    def _on_event(partition_context, event):
        nonlocal events_processed
        if event is None:
            return  # safety guard
        if events_processed >= config.max_events_per_run:
            return
        try:
            raw = event.body_as_str()
            data = json.loads(raw)
            collected.append(data)
            partition_context.update_checkpoint(event)
            events_processed += 1
        except Exception:
            logger.exception("Failed to parse EH event")

    try:
        # EH receive() blocks indefinitely — run it in a daemon thread so we
        # can enforce a fixed wall-clock budget and exit cleanly.
        #
        # starting_position:
        #   "@latest"  (default) — only events that arrive *after* this job
        #               connects. Correct for steady-state: the cron runs every
        #               5 min; any backlog from before this run was already
        #               consumed by the previous run (EH checkpoints the offset).
        #               Prevents replaying the full 1-day Basic-SKU retention
        #               window on every pod restart or redeploy.
        #   "-1"       — earliest retained offset (full replay). Activated by
        #               EXTRACTOR_REPLAY_FROM_START=true. Use once on initial
        #               deploy to catch up, then revert.
        #
        # receive_window_seconds (default 25s):
        #   Cold-start budget breakdown for a fresh Container Apps Job pod:
        #     container start          ~3-5s
        #     DefaultAzureCredential   ~2-4s   (managed identity token fetch)
        #     Key Vault secret fetch   ~3-6s   (3 secrets, sequential)
        #     AMQP handshake to EH     ~2-4s
        #     ─────────────────────────────
        #     total cold-start         ~10-19s
        #   With a 25s window the effective receive time is ~6-15s on cold
        #   starts and ~20s on warm restarts. The cron period is 5 min so
        #   total job wall-clock is ~35s — well within the 5-min slot.
        #   Override via RECEIVE_WINDOW_SECONDS env var. See ADR-0016.
        starting_position = "-1" if config.replay_from_start else "@latest"
        receive_thread = threading.Thread(
            target=eh_client.receive,
            kwargs={"on_event": _on_event, "starting_position": starting_position},
            daemon=True,
        )
        receive_start = time.monotonic()
        receive_thread.start()
        time.sleep(config.receive_window_seconds)
        receive_thread.join(timeout=10)
        logger.info("Receive window elapsed: %.1fs", time.monotonic() - receive_start)
    except Exception:
        logger.exception("Event Hubs receive error")
    finally:
        eh_client.close()

    logger.info("Consumed %d events from Event Hubs", events_processed)

    # Pre-flight dedup: query Cosmos for post_ids already extracted in a prior
    # run. The ingestor's 6h look-back window re-publishes the same posts on
    # every poll cycle; without this gate every re-published post would burn
    # one OpenAI call before the upsert silently overwrote the existing signal.
    all_post_ids = {e.get("post_id", "") for e in collected if e.get("post_id")}
    already_extracted = writer.get_extracted_post_ids(all_post_ids)
    if already_extracted:
        logger.info("Pre-flight dedup: skipping %d already-extracted posts", len(already_extracted))

    gated = 0
    skipped_dedup = 0
    for event_data in collected:
        if event_data.get("post_id") in already_extracted:
            skipped_dedup += 1
            continue
        try:
            signals = extractor.extract(event_data)
            if signals is None or len(signals) == 0:
                gated += 1
            else:
                written = writer.write_batch(signals)
                signals_written += written
                logger.debug(
                    "post=%s signals=%d written=%d",
                    event_data.get("post_id"), len(signals), written,
                )
        except Exception:
            logger.exception("Extraction failed for post %s", event_data.get("post_id"))

    logger.info("Gate stats: gated=%d skipped_dedup=%d passed=%d", gated, skipped_dedup, events_processed - gated - skipped_dedup)

    logger.info(
        "Extractor done. events=%d signals_written=%d",
        events_processed, signals_written,
    )


if __name__ == "__main__":
    main()

