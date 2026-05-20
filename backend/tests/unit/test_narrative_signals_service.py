"""Unit tests for backend/services/narrative/signals_service.py and the
``query_signal_events`` helper in cosmos_client.py.

Strategy: mock the Cosmos container at the cosmos_client boundary so no real
Cosmos calls are issued; assert the service correctly parses docs, computes
excess returns, and aggregates hit-rate / median across forward horizons.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from services.narrative import cosmos_client, signals_service


# ---------------------------------------------------------------------------
# Fake container — captures the most recent query + params, returns canned docs
# ---------------------------------------------------------------------------


class _FakeContainer:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self.last_query: str | None = None
        self.last_params: list[dict] | None = None
        self.last_kwargs: dict | None = None

    def query_items(self, query: str, **kwargs):  # noqa: ANN001
        self.last_query = query
        self.last_params = kwargs.get("parameters")
        self.last_kwargs = kwargs
        return list(self._docs)


def _doc(
    *,
    ticker: str = "NVDA",
    event_date: str = "2026-05-01",
    prev: int = 2,
    new: int = 3,
    confidence: float = 0.85,
    px0: float | None = 100.0,
    px5: float | None = 105.0,
    px10: float | None = 108.0,
    px20: float | None = 110.0,
    spy0: float | None = 500.0,
    spy5: float | None = 505.0,
    spy10: float | None = 510.0,
    spy20: float | None = 515.0,
    backfilled_at: str | None = "2026-05-22T22:00:00Z",
) -> dict:
    return {
        "id": f"{ticker}_{event_date}_h12_stage{prev}to{new}",
        "ticker": ticker,
        "event_date": event_date,
        "event_ts": f"{event_date}T12:00:00Z",
        "prev_stage": prev,
        "new_stage": new,
        "confidence": confidence,
        "breadth_score": 0.42,
        "breadth_delta": 0.05,
        "px_at_signal": px0,
        "px_t5": px5,
        "px_t10": px10,
        "px_t20": px20,
        "spy_at_signal": spy0,
        "spy_t5": spy5,
        "spy_t10": spy10,
        "spy_t20": spy20,
        "backfilled_at": backfilled_at,
    }


# ---------------------------------------------------------------------------
# query_signal_events — query shape + filter wiring
# ---------------------------------------------------------------------------


class TestQuerySignalEvents:
    def test_no_filters_returns_all_cross_partition(self) -> None:
        fc = _FakeContainer([_doc()])
        with patch.object(cosmos_client, "_get_signal_events", return_value=fc):
            rows = cosmos_client.query_signal_events(limit=50)
        assert len(rows) == 1
        assert fc.last_kwargs is not None
        assert fc.last_kwargs["enable_cross_partition_query"] is True
        # Limit injected as @limit param
        assert {"name": "@limit", "value": 50} in (fc.last_params or [])

    def test_since_filter_adds_event_date_clause(self) -> None:
        fc = _FakeContainer([])
        with patch.object(cosmos_client, "_get_signal_events", return_value=fc):
            cosmos_client.query_signal_events(since="2026-05-01")
        assert "c.event_date >= @since" in (fc.last_query or "")
        assert {"name": "@since", "value": "2026-05-01"} in (fc.last_params or [])

    def test_min_confidence_filter(self) -> None:
        fc = _FakeContainer([])
        with patch.object(cosmos_client, "_get_signal_events", return_value=fc):
            cosmos_client.query_signal_events(min_confidence=0.7)
        assert "c.confidence >= @min_conf" in (fc.last_query or "")
        assert {"name": "@min_conf", "value": 0.7} in (fc.last_params or [])

    def test_transition_filter_decomposes_into_int_params(self) -> None:
        fc = _FakeContainer([])
        with patch.object(cosmos_client, "_get_signal_events", return_value=fc):
            cosmos_client.query_signal_events(transition="2to3")
        assert "c.prev_stage = @prev_stage" in (fc.last_query or "")
        assert {"name": "@prev_stage", "value": 2} in (fc.last_params or [])
        assert {"name": "@new_stage", "value": 3} in (fc.last_params or [])

    def test_invalid_transition_returns_empty(self) -> None:
        fc = _FakeContainer([_doc()])
        with patch.object(cosmos_client, "_get_signal_events", return_value=fc):
            assert cosmos_client.query_signal_events(transition="badformat") == []

    def test_ticker_filter_uses_single_partition_query(self) -> None:
        fc = _FakeContainer([])
        with patch.object(cosmos_client, "_get_signal_events", return_value=fc):
            cosmos_client.query_signal_events(ticker="nvda")
        assert fc.last_kwargs is not None
        assert fc.last_kwargs["enable_cross_partition_query"] is False
        assert {"name": "@ticker", "value": "NVDA"} in (fc.last_params or [])

    def test_cosmos_exception_returns_empty_list(self) -> None:
        class _Boom:
            def query_items(self, *_a, **_kw):  # noqa: ANN001
                raise RuntimeError("simulated cosmos failure")

        with patch.object(cosmos_client, "_get_signal_events", return_value=_Boom()):
            assert cosmos_client.query_signal_events() == []


# ---------------------------------------------------------------------------
# _excess_return — pure math
# ---------------------------------------------------------------------------


class TestExcessReturn:
    def test_simple_positive_excess(self) -> None:
        # +5% ticker vs +1% SPY → +4% excess
        assert signals_service._excess_return(100, 105, 500, 505) == 0.04

    def test_negative_excess_when_ticker_underperforms(self) -> None:
        # +2% ticker vs +5% SPY → -3% excess
        result = signals_service._excess_return(100, 102, 500, 525)
        assert result is not None
        assert abs(result - (-0.03)) < 1e-9

    def test_missing_input_yields_none(self) -> None:
        assert signals_service._excess_return(None, 105, 500, 505) is None
        assert signals_service._excess_return(100, None, 500, 505) is None
        assert signals_service._excess_return(100, 105, None, 505) is None
        assert signals_service._excess_return(100, 105, 500, None) is None

    def test_zero_base_avoids_zero_division(self) -> None:
        assert signals_service._excess_return(0, 105, 500, 505) is None
        assert signals_service._excess_return(100, 105, 0, 505) is None


# ---------------------------------------------------------------------------
# compute_horizon_stats — aggregation
# ---------------------------------------------------------------------------


class TestHorizonStats:
    def test_no_events_yields_nulls_at_all_horizons(self) -> None:
        stats = signals_service.compute_horizon_stats([])
        assert [s.horizon_days for s in stats] == [5, 10, 20]
        for s in stats:
            assert s.n_complete == 0
            assert s.hit_rate is None
            assert s.median_excess_return is None

    def test_hit_rate_and_median_over_hydrated_events(self) -> None:
        # Build 3 events: +4%, -1%, +2% excess at T+5
        events = [
            signals_service._doc_to_event(_doc(px0=100, px5=105, spy0=500, spy5=505)),  # +4%
            signals_service._doc_to_event(_doc(px0=100, px5=99,  spy0=500, spy5=500)),  # -1%
            signals_service._doc_to_event(_doc(px0=100, px5=104, spy0=500, spy5=510)),  # +2%
        ]
        stats = signals_service.compute_horizon_stats(events)
        t5 = next(s for s in stats if s.horizon_days == 5)
        assert t5.n_complete == 3
        assert t5.hit_rate == 2 / 3  # 2 of 3 positive
        assert t5.median_excess_return == 0.02

    def test_excludes_unhydrated_events_from_denominator(self) -> None:
        events = [
            signals_service._doc_to_event(_doc(px0=100, px5=105, spy0=500, spy5=505)),
            signals_service._doc_to_event(_doc(
                px0=None, px5=None, spy0=None, spy5=None,
                px10=None, px20=None, spy10=None, spy20=None,
                backfilled_at=None,
            )),
        ]
        stats = signals_service.compute_horizon_stats(events)
        t5 = next(s for s in stats if s.horizon_days == 5)
        assert t5.n_complete == 1


# ---------------------------------------------------------------------------
# get_signals — async end-to-end with mocked query
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestGetSignals:
    def test_returns_events_and_stats(self) -> None:
        docs = [
            _doc(ticker="NVDA", event_date="2026-05-10"),
            _doc(ticker="AMD",  event_date="2026-05-09"),
        ]
        with patch.object(signals_service, "query_signal_events", return_value=docs):
            resp = _run(signals_service.get_signals(limit=10))
        assert resp.n_total == 2
        assert {e.ticker for e in resp.events} == {"NVDA", "AMD"}
        # All docs hydrated → all 3 horizons should have n_complete=2
        assert all(h.n_complete == 2 for h in resp.horizons)

    def test_passes_filters_through_to_query(self) -> None:
        captured: dict = {}

        def _fake_query(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            return []

        with patch.object(signals_service, "query_signal_events", side_effect=_fake_query):
            _run(signals_service.get_signals(
                since="2026-05-01",
                min_confidence=0.6,
                transition="2to3",
                ticker="NVDA",
                limit=25,
            ))
        assert captured == {
            "since": "2026-05-01",
            "min_confidence": 0.6,
            "transition": "2to3",
            "ticker": "NVDA",
            "limit": 25,
        }

    def test_doc_to_event_computes_transition_string(self) -> None:
        ev = signals_service._doc_to_event(_doc(prev=2, new=3))
        assert ev.transition == "2to3"

    def test_doc_to_event_uppercases_ticker(self) -> None:
        ev = signals_service._doc_to_event(_doc(ticker="nvda"))
        assert ev.ticker == "NVDA"
