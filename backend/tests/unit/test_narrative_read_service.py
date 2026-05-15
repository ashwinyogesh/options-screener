"""Unit tests for backend/services/narrative/read_service.py.

Mocks the cosmos_client query functions. No network calls.
Covers:
- _doc_to_acs conversion (full doc → AcsScore)
- _doc_to_acs fallbacks (missing components / scored_at / dominant_signal)
- get_acs_for_ticker: success, TickerNotTracked, NarrativeUnavailable
- get_top_tickers / get_emerging_tickers: success + error wrapping
- get_narrative / get_alerts: still 503 in Phase 6
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from services.narrative import read_service
from services.narrative.errors import (
    NarrativeUnavailable,
    TickerNotTracked,
)


def _run(coro):
    return asyncio.run(coro)


def _doc(**overrides: object) -> dict:
    base: dict = {
        "ticker": "NVDA",
        "acs": 62.5,
        "acs_ci_lower": 53.1,
        "acs_ci_upper": 71.9,
        "decay_acs": 60.0,
        "acs_components": {"A": 20.0, "B": 15.0, "C": 18.0, "D": 9.5, "E": 0.0},
        "acs_flags": ["gini_high"],
        "acs_scored_at": "2026-05-14T12:00:00+00:00",
        "dominant_signal": "researched_bull",
    }
    base.update(overrides)
    return base


# ---------- _doc_to_acs ----------


class TestDocToAcs:
    def test_full_doc_maps_all_fields(self) -> None:
        score = read_service._doc_to_acs(_doc())
        assert score.ticker == "NVDA"
        assert score.acs == 62.5
        assert score.acs_ci_lower == 53.1
        assert score.acs_ci_upper == 71.9
        assert score.decay_acs == 60.0
        assert score.dominant_signal == "researched_bull"
        assert score.flags == ["gini_high"]
        assert score.components.a_attention_persistence == 20.0
        assert score.components.e_market_confirmation == 0.0
        assert score.scored_at == datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)

    def test_missing_components_default_to_zero(self) -> None:
        score = read_service._doc_to_acs(_doc(acs_components={}))
        assert score.components.a_attention_persistence == 0.0
        assert score.components.b_contributor_quality == 0.0

    def test_missing_acs_components_key_safe(self) -> None:
        doc = _doc()
        del doc["acs_components"]
        score = read_service._doc_to_acs(doc)
        assert score.components.a_attention_persistence == 0.0

    def test_scored_at_falls_back_to_computed_at(self) -> None:
        doc = _doc()
        del doc["acs_scored_at"]
        doc["computed_at"] = "2026-05-13T08:00:00+00:00"
        score = read_service._doc_to_acs(doc)
        assert score.scored_at == datetime(2026, 5, 13, 8, 0, tzinfo=timezone.utc)

    def test_scored_at_falls_back_to_now_when_unparseable(self) -> None:
        before = datetime.now(tz=timezone.utc)
        score = read_service._doc_to_acs(_doc(acs_scored_at="not-a-date"))
        after = datetime.now(tz=timezone.utc)
        assert before <= score.scored_at <= after

    def test_handles_z_suffix(self) -> None:
        score = read_service._doc_to_acs(_doc(acs_scored_at="2026-05-14T12:00:00Z"))
        assert score.scored_at == datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)

    def test_dominant_signal_fallback_when_unset(self) -> None:
        doc = _doc()
        del doc["dominant_signal"]
        doc["conviction_researched_bull_ratio"] = 0.7
        doc["conviction_researched_bear_ratio"] = 0.2
        score = read_service._doc_to_acs(doc)
        assert score.dominant_signal == "researched_bull"

    def test_dominant_signal_unknown_when_no_data(self) -> None:
        doc = _doc()
        del doc["dominant_signal"]
        score = read_service._doc_to_acs(doc)
        assert score.dominant_signal == "unknown"

    def test_flags_default_empty(self) -> None:
        doc = _doc()
        del doc["acs_flags"]
        score = read_service._doc_to_acs(doc)
        assert score.flags == []


# ---------- get_acs_for_ticker ----------


class TestGetAcsForTicker:
    def test_returns_score_on_hit(self) -> None:
        with patch.object(read_service, "query_ticker", return_value=_doc()) as q:
            score = _run(read_service.get_acs_for_ticker("NVDA"))
        q.assert_called_once_with("NVDA")
        assert score.ticker == "NVDA"
        assert score.acs == 62.5

    def test_raises_ticker_not_tracked_when_doc_missing(self) -> None:
        with patch.object(read_service, "query_ticker", return_value=None):
            with pytest.raises(TickerNotTracked):
                _run(read_service.get_acs_for_ticker("ZZZZ"))

    def test_wraps_cosmos_errors_as_unavailable(self) -> None:
        with patch.object(read_service, "query_ticker", side_effect=RuntimeError("boom")):
            with pytest.raises(NarrativeUnavailable):
                _run(read_service.get_acs_for_ticker("NVDA"))


# ---------- get_top_tickers ----------


class TestGetTopTickers:
    def test_returns_mapped_list(self) -> None:
        docs = [_doc(ticker="NVDA"), _doc(ticker="TSLA", acs=55.0)]
        with patch.object(read_service, "query_top_acs", return_value=docs) as q:
            scores = _run(read_service.get_top_tickers(limit=10))
        q.assert_called_once_with(10)
        assert [s.ticker for s in scores] == ["NVDA", "TSLA"]
        assert scores[1].acs == 55.0

    def test_empty_when_no_docs(self) -> None:
        with patch.object(read_service, "query_top_acs", return_value=[]):
            scores = _run(read_service.get_top_tickers())
        assert scores == []

    def test_wraps_errors(self) -> None:
        with patch.object(read_service, "query_top_acs", side_effect=RuntimeError("boom")):
            with pytest.raises(NarrativeUnavailable):
                _run(read_service.get_top_tickers())


# ---------- get_emerging_tickers ----------


class TestGetEmergingTickers:
    def test_returns_mapped_list(self) -> None:
        docs = [_doc(ticker="ASML", acs=40.0)]
        with patch.object(read_service, "query_emerging", return_value=docs):
            scores = _run(read_service.get_emerging_tickers(limit=5))
        assert scores[0].ticker == "ASML"

    def test_wraps_errors(self) -> None:
        with patch.object(read_service, "query_emerging", side_effect=RuntimeError("boom")):
            with pytest.raises(NarrativeUnavailable):
                _run(read_service.get_emerging_tickers())


# ---------- get_narrative / get_alerts (still 503 in Phase 6) ----------


class TestNotYetImplemented:
    def test_get_narrative_unavailable(self) -> None:
        with pytest.raises(NarrativeUnavailable):
            _run(read_service.get_narrative(uuid4()))

    def test_get_alerts_unavailable(self) -> None:
        with pytest.raises(NarrativeUnavailable):
            _run(read_service.get_alerts())
