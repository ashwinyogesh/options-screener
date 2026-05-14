"""Unit tests for CosmosClassifierClient.

The azure.cosmos.CosmosClient and azure.identity.DefaultAzureCredential are
mocked at import path so no network or auth is attempted.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_container() -> MagicMock:
    return MagicMock()


@pytest.fixture
def cosmos_client(fake_container: MagicMock):
    from cosmos_client import CosmosClassifierClient

    with patch("cosmos_client.DefaultAzureCredential"), patch(
        "cosmos_client.CosmosClient"
    ) as fake_cosmos:
        fake_cosmos.return_value.get_database_client.return_value.get_container_client.return_value = (
            fake_container
        )
        client = CosmosClassifierClient(endpoint="https://x", database="narrative")
    return client


def test_fetch_unclassified_query_targets_undefined_conviction_state(
    cosmos_client, fake_container: MagicMock
) -> None:
    fake_container.query_items.return_value = iter([])

    cosmos_client.fetch_unclassified(batch_size=10)

    call = fake_container.query_items.call_args
    assert "NOT IS_DEFINED(c.conviction_state)" in call.kwargs["query"]
    assert call.kwargs["parameters"] == [{"name": "@batch_size", "value": 10}]
    assert call.kwargs["enable_cross_partition_query"] is True


def test_fetch_unclassified_filters_skip_ids(cosmos_client, fake_container: MagicMock) -> None:
    fake_container.query_items.return_value = iter(
        [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    )

    items = cosmos_client.fetch_unclassified(batch_size=10, skip_ids={"b"})

    assert [i["id"] for i in items] == ["a", "c"]


def test_fetch_missing_embeddings_query_targets_conviction_set_and_no_embedding(
    cosmos_client, fake_container: MagicMock
) -> None:
    fake_container.query_items.return_value = iter([])

    cosmos_client.fetch_missing_embeddings(batch_size=5)

    query = fake_container.query_items.call_args.kwargs["query"]
    assert "IS_DEFINED(c.conviction_state)" in query
    assert "NOT IS_DEFINED(c.embedding)" in query


def test_write_conviction_without_embedding_omits_embedding_fields(
    cosmos_client, fake_container: MagicMock
) -> None:
    cosmos_client.write_conviction(
        {"id": "1", "ticker": "NVDA"}, "researched_bull", 0.9
    )

    payload = fake_container.upsert_item.call_args.args[0]
    assert payload["conviction_state"] == "researched_bull"
    assert payload["conviction_confidence"] == 0.9
    assert "embedding" not in payload
    assert "embedding_model" not in payload


def test_write_conviction_with_embedding_includes_model(
    cosmos_client, fake_container: MagicMock
) -> None:
    cosmos_client.write_conviction(
        {"id": "1", "ticker": "NVDA"},
        "researched_bull",
        0.9,
        embedding=[0.1, 0.2],
        embedding_model="text-embedding-ada-002",
    )

    payload = fake_container.upsert_item.call_args.args[0]
    assert payload["embedding"] == [0.1, 0.2]
    assert payload["embedding_model"] == "text-embedding-ada-002"


def test_write_conviction_with_embedding_requires_model(
    cosmos_client, fake_container: MagicMock
) -> None:
    with pytest.raises(ValueError, match="embedding_model is required"):
        cosmos_client.write_conviction(
            {"id": "1"}, "researched_bull", 0.9, embedding=[0.1], embedding_model=None
        )
    fake_container.upsert_item.assert_not_called()


def test_write_embedding_upserts_doc_with_embedding_fields(
    cosmos_client, fake_container: MagicMock
) -> None:
    cosmos_client.write_embedding(
        {"id": "1", "ticker": "NVDA", "conviction_state": "researched_bull"},
        [0.1, 0.2, 0.3],
        "text-embedding-ada-002",
    )

    payload = fake_container.upsert_item.call_args.args[0]
    assert payload["embedding"] == [0.1, 0.2, 0.3]
    assert payload["embedding_model"] == "text-embedding-ada-002"
    assert payload["conviction_state"] == "researched_bull"
