"""Blob and Event Hubs writers for raw Reddit events.

Blob-first durability: every batch is written to Blob *before* it is
published to Event Hubs. If the EH publish fails, the data is already
durable and a future replay can re-publish from Blob. Event Hubs Basic
only retains 1 day, so this is the only durable backstop. See ADR-0014.
"""
from __future__ import annotations

import gzip
import io
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from azure.eventhub import EventData, EventHubProducerClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from schema import RawEvent

logger = logging.getLogger(__name__)


class BlobBatchWriter:
    """Writes batches of RawEvents to Blob as gzipped JSONL.

    Path: {container}/{subreddit}/{yyyy-MM-dd}/{kind}/{batch_id}.jsonl.gz
    """

    def __init__(self, account_name: str, container: str) -> None:
        url = f"https://{account_name}.blob.core.windows.net"
        self._client = BlobServiceClient(account_url=url, credential=DefaultAzureCredential())
        self._container = container

    def write_batch(self, subreddit: str, kind: str, events: list[RawEvent]) -> str:
        if not events:
            return ""
        date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        batch_id = str(uuid.uuid4())
        blob_path = f"{subreddit}/{date_part}/{kind}/{batch_id}.jsonl.gz"

        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for event in events:
                gz.write(event.to_json().encode("utf-8"))
                gz.write(b"\n")
        buf.seek(0)

        blob = self._client.get_blob_client(container=self._container, blob=blob_path)
        blob.upload_blob(buf, overwrite=False)
        logger.info("Wrote %d events to blob %s", len(events), blob_path)
        return blob_path


class EventHubPublisher:
    """Publishes RawEvents to a single Event Hub topic.

    Uses the AMQP client (not Kafka shim) since we only need a producer and
    Basic SKU supports both.
    """

    def __init__(self, namespace_fqdn: str, eventhub_name: str) -> None:
        self._producer = EventHubProducerClient(
            fully_qualified_namespace=namespace_fqdn,
            eventhub_name=eventhub_name,
            credential=DefaultAzureCredential(),
        )

    def publish_batch(self, events: Iterable[RawEvent]) -> int:
        batch = self._producer.create_batch()
        published = 0
        for event in events:
            data = EventData(event.to_json())
            try:
                batch.add(data)
            except ValueError:
                # Batch is full; send and start a new one.
                self._producer.send_batch(batch)
                published += len(batch)
                batch = self._producer.create_batch()
                batch.add(data)
        if len(batch) > 0:
            self._producer.send_batch(batch)
            published += len(batch)
        return published

    def close(self) -> None:
        self._producer.close()
