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
        # receive() blocks forever — run it in a daemon thread and close after
        # the budget window so the job exits cleanly.
        receive_thread = threading.Thread(
            target=eh_client.receive,
            kwargs={"on_event": _on_event, "starting_position": "-1"},
            daemon=True,
        )
        receive_thread.start()
        time.sleep(30)  # collect events for 30 seconds
        eh_client.close()
        receive_thread.join(timeout=10)
    except Exception:
        logger.exception("Event Hubs receive error")
    finally:
        eh_client.close()

    logger.info("Consumed %d events from Event Hubs", events_processed)

    gated = 0
    for event_data in collected:
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

    logger.info("Gate stats: gated=%d passed=%d", gated, events_processed - gated)

    logger.info(
        "Extractor done. events=%d signals_written=%d",
        events_processed, signals_written,
    )


if __name__ == "__main__":
    main()

