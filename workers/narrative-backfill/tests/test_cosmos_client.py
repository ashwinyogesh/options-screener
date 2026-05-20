"""Tests for cosmos_client (signal_events query + update)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cosmos_client import BackfillCosmosClient


def _make_client() -> tuple[BackfillCosmosClient, MagicMock]:
    client = BackfillCosmosClient.__new__(BackfillCosmosClient)
    client._signal_events = MagicMock()  # type: ignore[attr-defined]
    return client, client._signal_events  # type: ignore[attr-defined]


class TestFetchUnfilledEvents:
    def test_query_filters_and_limits(self) -> None:
        client, container = _make_client()
        container.query_items.return_value = iter([{"id": "AAPL_2026-05-01_h14_stage2to3"}])

        rows = client.fetch_unfilled_events(max_events=100)
        assert len(rows) == 1

        call = container.query_items.call_args
        query = call.kwargs.get("query", call.args[0] if call.args else "")
        assert "TOP @max_events" in query
        assert "backfilled_at" in query
        assert "event_date" in query

        params = {p["name"]: p["value"] for p in call.kwargs["parameters"]}
        assert params["@max_events"] == 100
        # Today is plugged in dynamically; just verify it looks like a date.
        assert len(params["@today"]) == len("YYYY-MM-DD")

        assert call.kwargs["enable_cross_partition_query"] is True


class TestUpdateEventPrices:
    def test_non_null_values_are_merged(self) -> None:
        client, container = _make_client()
        event = {"id": "AAPL_2026-05-01_h14_stage2to3", "ticker": "AAPL"}
        client.update_event_prices(
            event,
            ticker_prices={
                "px_at_signal": 187.42,
                "px_t5": 190.10,
                "px_t10": None,
                "px_t20": None,
            },
            spy_prices={
                "spy_at_signal": 580.0,
                "spy_t5": None,
                "spy_t10": None,
                "spy_t20": None,
            },
            mark_complete=False,
        )
        container.upsert_item.assert_called_once_with(event)
        assert event["px_at_signal"] == 187.42
        assert event["px_t5"] == 190.1
        assert "px_t10" not in event or event.get("px_t10") is None
        assert event["spy_at_signal"] == 580.0
        assert "backfilled_at" not in event

    def test_existing_filled_values_not_overwritten_by_none(self) -> None:
        """A second-pass backfill must not clobber a previously-set price."""
        client, _ = _make_client()
        event = {"id": "x", "ticker": "AAPL", "px_at_signal": 187.42}
        client.update_event_prices(
            event,
            ticker_prices={
                "px_at_signal": None,  # this fetch failed
                "px_t5": 190.10,
                "px_t10": None,
                "px_t20": None,
            },
            spy_prices={"spy_at_signal": None, "spy_t5": None, "spy_t10": None, "spy_t20": None},
            mark_complete=False,
        )
        # Existing value preserved.
        assert event["px_at_signal"] == 187.42
        # New value applied.
        assert event["px_t5"] == 190.10

    def test_mark_complete_stamps_backfilled_at(self) -> None:
        client, _ = _make_client()
        event = {"id": "x", "ticker": "AAPL"}
        client.update_event_prices(
            event,
            ticker_prices={
                "px_at_signal": 100.0, "px_t5": 101.0,
                "px_t10": 102.0, "px_t20": 103.0,
            },
            spy_prices={
                "spy_at_signal": 580.0, "spy_t5": 582.0,
                "spy_t10": 584.0, "spy_t20": 586.0,
            },
            mark_complete=True,
        )
        assert event["backfilled_at"] is not None
        assert event["backfilled_at"].endswith("Z")
        assert event["px_t20"] == 103.0
        assert event["spy_t20"] == 586.0

    def test_values_are_rounded_to_4_decimals(self) -> None:
        client, _ = _make_client()
        event = {"id": "x", "ticker": "AAPL"}
        client.update_event_prices(
            event,
            ticker_prices={
                "px_at_signal": 187.42378123, "px_t5": None,
                "px_t10": None, "px_t20": None,
            },
            spy_prices={"spy_at_signal": None, "spy_t5": None, "spy_t10": None, "spy_t20": None},
            mark_complete=False,
        )
        assert event["px_at_signal"] == 187.4238
