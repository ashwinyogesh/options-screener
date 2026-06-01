"""CRUD orchestration for DD Coach entries.

This module is the only thing the router talks to. It owns:
  - Cosmos read/write
  - Immutability: completed entries cannot be patched or deleted
  - Completion validation (delegates to DDEntryDoc.assert_completable)

The Cosmos partition key is `/ticker`, so every point-read and delete must
supply ticker. List operations use a cross-partition query — fine at the
single-user V1 volume.
"""
from __future__ import annotations

import logging
from typing import Any

from azure.cosmos import exceptions as cosmos_exceptions

from services.dd_coach.cosmos_client import get_container
from services.dd_coach.errors import (
    DDCoachUnavailable,
    DDEntryImmutable,
    DDEntryNotFound,
)
from services.dd_coach.models import (
    DEFAULT_USER_ID,
    DDEntryDoc,
    EntryStatus,
    PatchEntryInput,
    _now_iso,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_cosmos_call(op: str):  # decorator factory  # type: ignore[no-untyped-def]
    """Map azure.cosmos exceptions to domain errors.

    Used as a context-manager-style helper rather than a decorator so we can
    keep the original control flow obvious in each service function.
    """
    raise NotImplementedError  # not used — kept as a doc anchor for the convention


def _doc_to_entry(doc: dict[str, Any]) -> DDEntryDoc:
    return DDEntryDoc.model_validate(doc)


def _entry_to_doc(entry: DDEntryDoc) -> dict[str, Any]:
    # mode="json" so datetimes/enums serialize cleanly for Cosmos.
    return entry.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_draft(
    ticker: str,
    *,
    data_card_snapshot: dict[str, Any] | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> DDEntryDoc:
    """Create a new draft entry and persist it."""
    entry = DDEntryDoc(
        ticker=ticker,
        user_id=user_id,
        data_card_snapshot=data_card_snapshot or {},
    )
    container = get_container()
    container.create_item(_entry_to_doc(entry))
    logger.info("dd_coach.create_draft ticker=%s id=%s", entry.ticker, entry.id)
    return entry


def get_entry(entry_id: str, ticker: str) -> DDEntryDoc:
    """Point-read by id + partition key (ticker)."""
    container = get_container()
    try:
        doc = container.read_item(item=entry_id, partition_key=ticker.upper())
    except cosmos_exceptions.CosmosResourceNotFoundError as exc:
        raise DDEntryNotFound(
            f"DD entry not found: id={entry_id} ticker={ticker}",
        ) from exc
    return _doc_to_entry(doc)


def list_entries(
    *,
    user_id: str = DEFAULT_USER_ID,
    ticker: str | None = None,
    status: EntryStatus | None = None,
    limit: int = 50,
) -> list[DDEntryDoc]:
    """List entries for the user, newest first.

    `ticker` filter is a point partition lookup (cheap); otherwise this is a
    cross-partition query (acceptable for V1 single-user volume).
    """
    container = get_container()

    where = ["c.user_id = @user_id"]
    params: list[dict[str, Any]] = [{"name": "@user_id", "value": user_id}]
    if ticker is not None:
        where.append("c.ticker = @ticker")
        params.append({"name": "@ticker", "value": ticker.upper()})
    if status is not None:
        where.append("c.status = @status")
        params.append({"name": "@status", "value": status.value})

    query = (
        "SELECT * FROM c WHERE "
        + " AND ".join(where)
        + " ORDER BY c.created_at DESC"
        + f" OFFSET 0 LIMIT {int(limit)}"
    )
    items = container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=ticker is None,
    )
    return [_doc_to_entry(doc) for doc in items]


def patch_entry(
    entry_id: str,
    ticker: str,
    patch: PatchEntryInput,
) -> DDEntryDoc:
    """Partial update for a draft. Completed entries are immutable (409)."""
    existing = get_entry(entry_id, ticker)
    if existing.is_completed():
        raise DDEntryImmutable(
            f"Entry {entry_id} is completed and cannot be modified.",
        )

    # Merge: replace whole sub-objects when provided (frontend sends full
    # screen-state on autosave).
    if patch.data_card_snapshot is not None:
        existing.data_card_snapshot = patch.data_card_snapshot
    if patch.answers is not None:
        existing.answers = patch.answers
    if patch.valuation is not None:
        existing.valuation = patch.valuation
    if patch.sizing is not None:
        existing.sizing = patch.sizing
    existing.updated_at = _now_iso()

    container = get_container()
    container.replace_item(item=entry_id, body=_entry_to_doc(existing))
    return existing


def complete_entry(entry_id: str, ticker: str) -> DDEntryDoc:
    """Mark a draft as completed (immutable). Re-completing is a no-op idempotent
    error (409) so the client sees a clean signal."""
    existing = get_entry(entry_id, ticker)
    if existing.is_completed():
        raise DDEntryImmutable(f"Entry {entry_id} is already completed.")

    existing.assert_completable()
    existing.status = EntryStatus.COMPLETED
    existing.completed_at = _now_iso()
    existing.updated_at = existing.completed_at

    container = get_container()
    container.replace_item(item=entry_id, body=_entry_to_doc(existing))
    logger.info("dd_coach.complete_entry id=%s ticker=%s", entry_id, ticker)
    return existing


def delete_entry(entry_id: str, ticker: str) -> None:
    """Delete a draft. Completed entries are permanent (409)."""
    existing = get_entry(entry_id, ticker)
    if existing.is_completed():
        raise DDEntryImmutable(
            f"Entry {entry_id} is completed and cannot be deleted.",
        )
    container = get_container()
    container.delete_item(item=entry_id, partition_key=ticker.upper())


__all__ = [
    "DDCoachUnavailable",
    "complete_entry",
    "create_draft",
    "delete_entry",
    "get_entry",
    "list_entries",
    "patch_entry",
]
