"""Unit tests for services/dd_coach/entry_service.py.

The service is the only code that talks to Cosmos; routers just convert.
These tests stub `get_container` with an in-memory fake so the full
create/list/patch/complete/delete + immutability rules are exercised
without any network or Azure credentials.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from azure.cosmos import exceptions as cosmos_exceptions

from services.dd_coach import entry_service
from services.dd_coach.errors import (
    DDEntryImmutable,
    DDEntryInvalid,
    DDEntryNotFound,
)
from services.dd_coach.models import (
    Answers,
    EntryStatus,
    PatchEntryInput,
    Sizing,
    StomachAnswer,
    UserCall,
    Valuation,
    ValuationMethod,
)


# ---------------------------------------------------------------------------
# Fake Cosmos container
# ---------------------------------------------------------------------------


class FakeContainer:
    """In-memory stand-in for an azure.cosmos.ContainerProxy.

    Stores docs keyed by id only (V1 entries have unique ids regardless of
    ticker partition). Implements just the surface the service calls.
    """

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self.docs[body["id"]] = dict(body)
        return self.docs[body["id"]]

    def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        doc = self.docs.get(item)
        if doc is None or doc["ticker"] != partition_key:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found",
            )
        return dict(doc)

    def replace_item(self, item: str, body: dict[str, Any]) -> dict[str, Any]:
        if item not in self.docs:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found",
            )
        self.docs[item] = dict(body)
        return self.docs[item]

    def delete_item(self, item: str, partition_key: str) -> None:
        doc = self.docs.get(item)
        if doc is None or doc["ticker"] != partition_key:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found",
            )
        del self.docs[item]

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]],
        enable_cross_partition_query: bool = False,
    ) -> list[dict[str, Any]]:
        params = {p["name"]: p["value"] for p in parameters}
        results = list(self.docs.values())
        if "@user_id" in params:
            results = [d for d in results if d.get("user_id") == params["@user_id"]]
        if "@ticker" in params:
            results = [d for d in results if d.get("ticker") == params["@ticker"]]
        if "@status" in params:
            results = [d for d in results if d.get("status") == params["@status"]]
        results.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return results


@pytest.fixture
def fake_container() -> FakeContainer:
    fc = FakeContainer()
    with patch.object(entry_service, "get_container", return_value=fc):
        yield fc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completable_patch() -> PatchEntryInput:
    """A PatchEntryInput with every required-for-completion field populated."""
    return PatchEntryInput(
        answers=Answers(
            q1_business="Cloud GPU infra rental.",
            q2_revenue_model="$/GPU-hour, multi-year customer contracts.",
            q3_upside="AI compute demand outpaces hyperscaler capex.",
            q4_risks="Customer concentration; GPU supply timing.",
        ),
        valuation=Valuation(
            method=ValuationMethod.MATURITY_DISCOUNT,
            user_call=UserCall.FAIR,
            reasoning="Discounted maturity case priced in.",
        ),
        sizing=Sizing(
            planned_dollars=2000.0,
            stomach_answer=StomachAnswer.YES,
            final_dollars=2000.0,
        ),
    )


# ---------------------------------------------------------------------------
# create / get / list
# ---------------------------------------------------------------------------


class TestCreateAndGet:
    def test_create_persists_draft_with_uppercased_ticker(
        self, fake_container: FakeContainer,
    ) -> None:
        entry = entry_service.create_draft("nbis")

        assert entry.ticker == "NBIS"
        assert entry.status == EntryStatus.DRAFT.value or entry.status == EntryStatus.DRAFT
        assert entry.id in fake_container.docs
        stored = fake_container.docs[entry.id]
        assert stored["ticker"] == "NBIS"
        assert stored["user_id"] == "default"

    def test_get_entry_returns_persisted_doc(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        loaded = entry_service.get_entry(created.id, "NBIS")
        assert loaded.id == created.id
        assert loaded.ticker == "NBIS"

    def test_get_entry_unknown_id_raises_not_found(
        self, fake_container: FakeContainer,
    ) -> None:
        with pytest.raises(DDEntryNotFound):
            entry_service.get_entry("no-such-id", "NBIS")

    def test_get_entry_wrong_partition_raises_not_found(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        with pytest.raises(DDEntryNotFound):
            entry_service.get_entry(created.id, "AAPL")


class TestList:
    def test_list_returns_newest_first(
        self, fake_container: FakeContainer,
    ) -> None:
        a = entry_service.create_draft("NBIS")
        # Force ordering by overwriting created_at since the in-memory store
        # might otherwise tie at the same microsecond.
        fake_container.docs[a.id]["created_at"] = "2026-01-01T00:00:00+00:00"
        b = entry_service.create_draft("IONQ")
        fake_container.docs[b.id]["created_at"] = "2026-02-01T00:00:00+00:00"

        items = entry_service.list_entries()
        assert [e.ticker for e in items] == ["IONQ", "NBIS"]

    def test_list_filters_by_ticker(
        self, fake_container: FakeContainer,
    ) -> None:
        entry_service.create_draft("NBIS")
        entry_service.create_draft("IONQ")
        items = entry_service.list_entries(ticker="ionq")
        assert len(items) == 1
        assert items[0].ticker == "IONQ"

    def test_list_filters_by_status(
        self, fake_container: FakeContainer,
    ) -> None:
        d = entry_service.create_draft("NBIS")
        entry_service.patch_entry(d.id, "NBIS", _completable_patch())
        entry_service.complete_entry(d.id, "NBIS")
        entry_service.create_draft("IONQ")  # leave as draft

        completed = entry_service.list_entries(status=EntryStatus.COMPLETED)
        assert [e.ticker for e in completed] == ["NBIS"]
        drafts = entry_service.list_entries(status=EntryStatus.DRAFT)
        assert [e.ticker for e in drafts] == ["IONQ"]


# ---------------------------------------------------------------------------
# patch / complete / delete + immutability
# ---------------------------------------------------------------------------


class TestPatch:
    def test_patch_updates_answers_only(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        patched = entry_service.patch_entry(
            created.id,
            "NBIS",
            PatchEntryInput(answers=Answers(q1_business="Cloud GPU.")),
        )
        assert patched.answers.q1_business == "Cloud GPU."
        # Other fields untouched.
        assert patched.sizing.final_dollars is None

    def test_patch_bumps_updated_at(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        original_updated = created.updated_at
        patched = entry_service.patch_entry(
            created.id,
            "NBIS",
            PatchEntryInput(answers=Answers(q1_business="x")),
        )
        assert patched.updated_at >= original_updated

    def test_patch_on_completed_entry_raises_immutable(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        entry_service.patch_entry(created.id, "NBIS", _completable_patch())
        entry_service.complete_entry(created.id, "NBIS")

        with pytest.raises(DDEntryImmutable):
            entry_service.patch_entry(
                created.id,
                "NBIS",
                PatchEntryInput(answers=Answers(q1_business="changed")),
            )


class TestComplete:
    def test_complete_marks_status_and_sets_completed_at(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        entry_service.patch_entry(created.id, "NBIS", _completable_patch())
        done = entry_service.complete_entry(created.id, "NBIS")

        assert done.is_completed()
        assert done.completed_at is not None

    def test_complete_with_missing_required_fields_raises_invalid(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        # No patch — answers empty, sizing empty.
        with pytest.raises(DDEntryInvalid):
            entry_service.complete_entry(created.id, "NBIS")

    def test_complete_already_completed_raises_immutable(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        entry_service.patch_entry(created.id, "NBIS", _completable_patch())
        entry_service.complete_entry(created.id, "NBIS")
        with pytest.raises(DDEntryImmutable):
            entry_service.complete_entry(created.id, "NBIS")


class TestDelete:
    def test_delete_draft(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        entry_service.delete_entry(created.id, "NBIS")
        assert created.id not in fake_container.docs

    def test_delete_completed_raises_immutable(
        self, fake_container: FakeContainer,
    ) -> None:
        created = entry_service.create_draft("NBIS")
        entry_service.patch_entry(created.id, "NBIS", _completable_patch())
        entry_service.complete_entry(created.id, "NBIS")
        with pytest.raises(DDEntryImmutable):
            entry_service.delete_entry(created.id, "NBIS")

    def test_delete_unknown_raises_not_found(
        self, fake_container: FakeContainer,
    ) -> None:
        with pytest.raises(DDEntryNotFound):
            entry_service.delete_entry("no-such-id", "NBIS")
