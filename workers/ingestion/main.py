"""Ingestion worker entry point.

Long-running poll loop: every `poll_interval_seconds`, iterate the configured
subreddits, fetch new posts + top-level comments via PRAW, write each
subreddit's batch to Blob (durability), then publish to Event Hubs.

Failure semantics:
- Per-subreddit failures are logged and skipped; one bad subreddit does not
  crash the worker.
- If Blob write succeeds but EH publish fails, the data is durable; an
  out-of-band replay tool (Phase 2) can re-publish from Blob. We do NOT
  delete or move blobs that fail to publish.
"""
from __future__ import annotations

import logging
import sys
import time

from config import all_subreddits, load_from_env
from reddit_poller import RateBudget, RedditPoller
from secrets import fetch_secrets
from writers import BlobBatchWriter, EventHubPublisher

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def run() -> None:
    config = load_from_env()
    _setup_logging(config.log_level)
    logger.info("Starting narrative ingestion worker (Phase 1)")

    secrets = fetch_secrets(config.keyvault_uri)
    poller = RedditPoller(
        client_id=secrets.reddit_client_id,
        client_secret=secrets.reddit_client_secret,
        user_agent=secrets.reddit_user_agent,
        author_salt=secrets.reddit_author_salt,
    )
    blob_writer = BlobBatchWriter(
        account_name=config.blob_account_name,
        container=config.blob_container,
    )
    publisher = EventHubPublisher(
        namespace_fqdn=config.event_hub_namespace,
        eventhub_name=config.raw_events_hub,
    )
    budget = RateBudget(requests_per_minute=config.reddit_rate_limit_per_min)

    subreddits = all_subreddits()
    logger.info("Tracking %d subreddits", len(subreddits))

    try:
        while True:
            cycle_start = time.monotonic()
            for sub in subreddits:
                try:
                    budget.consume(2)  # 1 listing + 1 expansion
                    events = list(poller.poll_subreddit(sub))
                except Exception:
                    logger.exception("Poll failed for r/%s; skipping", sub)
                    continue

                if not events:
                    continue

                # Split posts and comments so blob paths are self-describing.
                posts = [e for e in events if e.kind == "post"]
                comments = [e for e in events if e.kind == "comment"]
                try:
                    if posts:
                        blob_writer.write_batch(sub, "post", posts)
                    if comments:
                        blob_writer.write_batch(sub, "comment", comments)
                except Exception:
                    logger.exception("Blob write failed for r/%s; skipping EH publish", sub)
                    continue

                try:
                    published = publisher.publish_batch(events)
                    logger.info("Published %d events for r/%s", published, sub)
                except Exception:
                    # Blob is the source of truth; data is safe. Replay later.
                    logger.exception("EH publish failed for r/%s; data durable in blob", sub)

            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, config.poll_interval_seconds - elapsed)
            logger.info("Cycle done in %.1fs; sleeping %.1fs", elapsed, sleep_for)
            time.sleep(sleep_for)
    finally:
        publisher.close()


if __name__ == "__main__":
    run()
