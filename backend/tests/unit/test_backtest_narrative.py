"""Unit tests for scripts/backtest_narrative.py.

Pure-math coverage:
- evaluate() IC calculation, threshold pass/fail
- evaluate() handles n < 10 (returns NaN, no crash)
- build_pairs() filters docs missing prices / acs
- forward_return() handles missing tickers and short price series

yfinance is mocked. No network calls.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# Importable as a module: ``scripts/`` is a flat dir, not a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import backtest_narrative as bn  # noqa: E402


def test_evaluate_perfectly_correlated_pairs_pass_threshold() -> None:
    # acs increasing, forward_return increasing → perfect rank correlation
    pairs = [bn.BacktestPair(f"T{i}", "2026-01-01", float(i), float(i) * 0.01)
             for i in range(20)]
    result = bn.evaluate(pairs, ic_threshold=0.04)
    assert result.n == 20
    assert result.ic_spearman == pytest.approx(1.0)
    assert result.passes_threshold is True
    assert result.decile_spread > 0


def test_evaluate_anti_correlated_pairs_fail() -> None:
    pairs = [bn.BacktestPair(f"T{i}", "2026-01-01", float(i), -float(i) * 0.01)
             for i in range(20)]
    result = bn.evaluate(pairs)
    assert result.ic_spearman == pytest.approx(-1.0)
    assert result.passes_threshold is False


def test_evaluate_small_sample_returns_nan() -> None:
    pairs = [bn.BacktestPair("X", "2026-01-01", 1.0, 0.02)]
    result = bn.evaluate(pairs)
    assert result.n == 1
    assert math.isnan(result.ic_spearman)
    assert result.passes_threshold is False


def test_forward_return_missing_ticker_returns_none() -> None:
    closes = pd.DataFrame(
        {"NVDA": [100.0, 101.0, 102.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-15", "2026-02-15"]),
    )
    assert bn.forward_return(closes, "MISSING", "2026-01-01", 30) is None


def test_forward_return_computes_first_available_after_target() -> None:
    closes = pd.DataFrame(
        {"NVDA": [100.0, 110.0]},
        index=pd.to_datetime(["2026-01-02", "2026-02-03"]),  # T+32
    )
    fr = bn.forward_return(closes, "NVDA", "2026-01-01", 30)
    assert fr == pytest.approx(0.10)


def test_forward_return_returns_none_when_target_in_future() -> None:
    closes = pd.DataFrame(
        {"NVDA": [100.0]},
        index=pd.to_datetime(["2026-01-02"]),
    )
    assert bn.forward_return(closes, "NVDA", "2026-01-01", 30) is None


def test_ensure_acs_uses_existing_value() -> None:
    assert bn.ensure_acs({"acs": 42.0, "ticker": "X", "bucket_date": "2026-01-01"}) == 42.0


def test_ensure_acs_computes_when_missing() -> None:
    doc = {
        "ticker": "X",
        "bucket_date": "2026-01-01",
        "decay_weighted_density_14d": 0.5,
    }
    acs = bn.ensure_acs(doc)
    assert acs is not None
    assert 0.0 <= acs <= 100.0


def test_build_pairs_drops_docs_without_prices() -> None:
    docs = [
        {"ticker": "NVDA", "bucket_date": "2026-01-02", "acs": 50.0},
        {"ticker": "NOPRICE", "bucket_date": "2026-01-02", "acs": 60.0},
    ]
    fake = pd.DataFrame(
        {"NVDA": [100.0, 110.0]},
        index=pd.to_datetime(["2026-01-02", "2026-02-02"]),
    )
    with patch.object(bn, "fetch_price_panel", return_value=fake):
        pairs = bn.build_pairs(docs, horizon_days=30)
    assert len(pairs) == 1
    assert pairs[0].ticker == "NVDA"
    assert pairs[0].forward_return == pytest.approx(0.10)


def test_build_pairs_empty_input() -> None:
    assert bn.build_pairs([], horizon_days=30) == []
