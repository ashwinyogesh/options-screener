"""Raw event schema for the narrative pipeline.

This is the wire format published to Event Hubs `reddit-raw-events` AND
written to Blob `reddit-raw/{subreddit}/{date}/{kind}/{batch_id}.jsonl.gz`.

See docs/NARRATIVE_METHODOLOGY.md and ADR-0013. The schema is intentionally
flat; downstream extractors and aggregators depend on these field names.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

EventKind = Literal["post", "comment"]


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    source: str
    subreddit: str
    post_id: str
    parent_id: str | None
    author_hash: str
    created_utc: int
    body: str
    score: int
    awards: int
    flair: str | None
    ingested_at: str
    kind: EventKind
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def new_event_id() -> str:
        return str(uuid.uuid4())

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))
