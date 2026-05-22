"""Read precomputed screener results from Cosmos (ADR-0024).

This module is the backend read path for the precomputed screener containers.
It replaces the on-demand yfinance fan-out in the GET /scan endpoints with
fast (<200 ms) Cosmos reads.

The three public functions follow the same DTE / capital filtering logic as
the live scan endpoints so callers receive identically shaped results.

Fallback policy: if the container is empty (worker has never run), the
function raises ``ScreenerStoreEmpty``; the router returns HTTP 503.
No silent fallback to live scan — that would mask worker failures.

Env vars (same convention as narrative/cosmos_client.py):
    NARRATIVE_COSMOS_ENDPOINT   (preferred — backend App Service env name)
    COSMOS_ENDPOINT             (fallback — worker / Bicep env name)
    NARRATIVE_COSMOS_DB / COSMOS_DB   (default "narrative")
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from services.csp_service import CspResult, CspStrikeResult
from services.cc_service import CcResult, CcStrikeResult
from services.ditm_service import DitmResult, DitmStrikeResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy Cosmos client (same pattern as narrative/cosmos_client.py)
# ---------------------------------------------------------------------------

_client: CosmosClient | None = None
_containers: dict[str, Any] = {}

_CONTAINER_MAP = {
    "csp": "screener_csp",
    "cc": "screener_cc",
    "ditm": "screener_ditm",
    "swing": "screener_swing",
}


def _get_container(strategy: str):
    global _client
    if strategy in _containers:
        return _containers[strategy]

    endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
    db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
    if not endpoint:
        raise RuntimeError(
            "Cosmos endpoint not set: configure NARRATIVE_COSMOS_ENDPOINT "
            "or COSMOS_ENDPOINT on this process."
        )

    if _client is None:
        _client = CosmosClient(endpoint, credential=DefaultAzureCredential())

    container = (
        _client.get_database_client(db_name)
        .get_container_client(_CONTAINER_MAP[strategy])
    )
    _containers[strategy] = container
    return container


# ---------------------------------------------------------------------------
# Domain exception
# ---------------------------------------------------------------------------

class ScreenerStoreEmpty(Exception):
    """Raised when the precomputed container has no docs (worker not yet run)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_csp_results(
    tickers: list[str],
    min_dte: int,
    max_dte: int,
    top_n: int,
    max_capital: Optional[float],
) -> tuple[list[CspResult], str | None, float | None]:
    """Return precomputed CSP results filtered to [min_dte, max_dte] and max_capital.

    Returns:
        (rows, last_updated_at, oldest_age_s)
        rows             — sorted by best_csp_score desc, sliced to top_n
        last_updated_at  — ISO UTC of the newest doc, or None
        oldest_age_s     — seconds since oldest doc was written, or None
    """
    docs = _fetch_docs("csp", tickers)
    rows: list[CspResult] = []
    for doc in docs:
        result_data = doc.get("result")
        if not result_data:
            continue
        for row_dict in result_data.get("rows", []):
            row = _csp_from_dict(row_dict)
            if not (min_dte <= row.dte <= max_dte):
                continue
            if max_capital is not None:
                row = _filter_csp_strikes_by_capital(row, max_capital)
                if not row.strikes:
                    continue
            rows.append(row)

    rows.sort(key=lambda r: r.best_csp_score, reverse=True)
    last_updated, oldest_age = _timestamps(docs)
    return rows[:top_n], last_updated, oldest_age


def get_cc_results(
    tickers: list[str],
    min_dte: int,
    max_dte: int,
    top_n: int,
) -> tuple[list[CcResult], str | None, float | None]:
    """Return precomputed CC results filtered to [min_dte, max_dte]."""
    docs = _fetch_docs("cc", tickers)
    rows: list[CcResult] = []
    for doc in docs:
        result_data = doc.get("result")
        if not result_data:
            continue
        for row_dict in result_data.get("rows", []):
            row = _cc_from_dict(row_dict)
            if min_dte <= row.dte <= max_dte:
                rows.append(row)

    rows.sort(key=lambda r: r.best_cc_score, reverse=True)
    last_updated, oldest_age = _timestamps(docs)
    return rows[:top_n], last_updated, oldest_age


def get_ditm_results(
    tickers: list[str],
    min_dte: int,
    max_dte: int,
    top_n: int,
) -> tuple[list[DitmResult], dict[str, Any], str | None, float | None]:
    """Return precomputed DITM results and macro context.

    Returns:
        (rows, macro_fields, last_updated_at, oldest_age_s)
        macro_fields — dict with macro_pass, vix_level, vix_5d_change,
                       spy_above_sma200 extracted from the freshest doc
    """
    docs = _fetch_docs("ditm", tickers)
    rows: list[DitmResult] = []
    macro_fields: dict[str, Any] = {
        "macro_pass": True,
        "vix_level": None,
        "vix_5d_change": None,
        "spy_above_sma200": True,
    }

    for doc in docs:
        result_data = doc.get("result")
        if not result_data:
            continue
        # Macro fields are stamped on every doc; use the first valid one.
        if doc.get("macro_pass") is not None and macro_fields["vix_level"] is None:
            macro_fields = {
                "macro_pass": doc.get("macro_pass", True),
                "vix_level": doc.get("vix_level"),
                "vix_5d_change": doc.get("vix_5d_change"),
                "spy_above_sma200": doc.get("spy_above_sma200", True),
            }
        for row_dict in result_data.get("rows", []):
            row = _ditm_from_dict(row_dict)
            if min_dte <= row.dte <= max_dte:
                rows.append(row)

    rows.sort(key=lambda r: r.best_ditm_score, reverse=True)
    last_updated, oldest_age = _timestamps(docs)
    return rows[:top_n], macro_fields, last_updated, oldest_age


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_docs(
    strategy: str,
    tickers: list[str],
    run_id: str | None = None,
    allow_empty_filtered: bool = False,
) -> list[dict[str, Any]]:
    """Fetch precomputed docs for *tickers* via per-partition point reads."""
    container = _get_container(strategy)
    docs = []
    found_any = False
    for ticker in tickers:
        try:
            doc = container.read_item(item=ticker, partition_key=ticker)
            found_any = True
            if run_id is not None and doc.get("run_id") != run_id:
                continue
            docs.append(doc)
        except Exception:
            logger.debug("No precomputed doc for %s/%s", strategy, ticker)

    if not found_any:
        raise ScreenerStoreEmpty(
            f"No precomputed docs found for strategy={strategy!r}. "
            "The background worker has not populated the container yet."
        )
    if not docs and not allow_empty_filtered:
        raise ScreenerStoreEmpty(
            f"No precomputed docs found for strategy={strategy!r}. "
            "The background worker has not populated the container yet."
        )
    return docs


def _latest_run_id(strategy: str) -> str | None:
    """Return newest run_id for strategy, or None if run_id is absent."""
    container = _get_container(strategy)
    query = (
        "SELECT TOP 1 c.run_id, c.computed_at FROM c "
        "WHERE IS_DEFINED(c.run_id) "
        "ORDER BY c.computed_at DESC"
    )
    try:
        items = list(
            container.query_items(
                query=query,
                enable_cross_partition_query=True,
            )
        )
    except Exception:
        logger.warning("Failed to query latest run_id for %s", strategy, exc_info=True)
        return None
    if not items:
        return None
    return items[0].get("run_id")


def _timestamps(docs: list[dict[str, Any]]) -> tuple[str | None, float | None]:
    """Return (newest computed_at ISO string, oldest age in seconds)."""
    if not docs:
        return None, None
    now = datetime.now(tz=timezone.utc)
    timestamps: list[datetime] = []
    for doc in docs:
        ts_str = doc.get("computed_at")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                timestamps.append(ts)
            except ValueError:
                pass
    if not timestamps:
        return None, None
    newest = max(timestamps)
    oldest = min(timestamps)
    oldest_age_s = (now - oldest).total_seconds()
    return newest.isoformat(), oldest_age_s


# ---------------------------------------------------------------------------
# Dataclass reconstruction from stored dicts
# ---------------------------------------------------------------------------

def _csp_from_dict(d: dict[str, Any]) -> CspResult:
    strikes = [
        CspStrikeResult(
            strike=s["strike"],
            delta=s["delta"],
            premium=s["premium"],
            annualized_return=s["annualized_return"],
            bid_ask_spread_pct=s.get("bid_ask_spread_pct"),
            env_score=s["env_score"],
            strike_score=s["strike_score"],
            csp_score=s["csp_score"],
            env_detail=s.get("env_detail", ""),
            strike_detail=s.get("strike_detail", ""),
            is_best=s.get("is_best", False),
            iv_fallback=s.get("iv_fallback", False),
            stale_premium=s.get("stale_premium", False),
            iv_hv_ratio=s.get("iv_hv_ratio"),
            dist_pct=s.get("dist_pct"),
            em_buffer_pct=s.get("em_buffer_pct"),
            otm_pct=s.get("otm_pct", 0.0),
            lq_count=s.get("lq_count", 0),
            roc_annualized=s.get("roc_annualized"),
            iv_stale=s.get("iv_stale", False),
        )
        for s in d.get("strikes", [])
    ]
    return CspResult(
        symbol=d["symbol"],
        price=d["price"],
        bb_upper=d["bb_upper"],
        bb_middle=d["bb_middle"],
        bb_lower=d["bb_lower"],
        sma_ratio=d["sma_ratio"],
        rsi=d["rsi"],
        iv_rank=d.get("iv_rank"),
        iv_percentile=d.get("iv_percentile"),
        earnings_date=d.get("earnings_date"),
        earnings_within_dte=d.get("earnings_within_dte", False),
        vol_support_126_1=d.get("vol_support_126_1"),
        vol_support_126_2=d.get("vol_support_126_2"),
        vol_support_126_3=d.get("vol_support_126_3"),
        dte=d["dte"],
        expiration=d["expiration"],
        strikes=strikes,
        best_csp_score=d.get("best_csp_score", 0.0),
        using_hv_fallback=d.get("using_hv_fallback", False),
        expected_move=d.get("expected_move", 0.0),
        dist_from_52w_high_pct=d.get("dist_from_52w_high_pct", 0.0),
        chain_median_oi=d.get("chain_median_oi", 0.0),
    )


def _filter_csp_strikes_by_capital(row: CspResult, max_capital: float) -> CspResult:
    """Return a copy of *row* with strikes filtered to strike×100 ≤ max_capital."""
    filtered = [s for s in row.strikes if s.strike * 100 <= max_capital]
    if not filtered:
        return CspResult(**{**vars(row), "strikes": [], "best_csp_score": 0.0})
    best_score = max(s.csp_score for s in filtered)
    # Mark is_best on the highest-scoring strike after filtering
    updated_strikes = [
        CspStrikeResult(**{**vars(s), "is_best": s.csp_score == best_score})
        for s in filtered
    ]
    import dataclasses
    return dataclasses.replace(row, strikes=updated_strikes, best_csp_score=best_score)


def _cc_from_dict(d: dict[str, Any]) -> CcResult:
    strikes = [
        CcStrikeResult(
            strike=s["strike"],
            delta=s["delta"],
            premium=s["premium"],
            annualized_return=s["annualized_return"],
            bid_ask_spread_pct=s.get("bid_ask_spread_pct"),
            env_score=s["env_score"],
            strike_score=s["strike_score"],
            cc_score=s["cc_score"],
            env_detail=s.get("env_detail", ""),
            strike_detail=s.get("strike_detail", ""),
            is_best=s.get("is_best", False),
            iv_fallback=s.get("iv_fallback", False),
            stale_premium=s.get("stale_premium", False),
            iv_hv_ratio=s.get("iv_hv_ratio"),
            dist_pct=s.get("dist_pct"),
            em_buffer_pct=s.get("em_buffer_pct"),
            otm_pct=s.get("otm_pct", 0.0),
            lq_count=s.get("lq_count", 0),
            roc_annualized=s.get("roc_annualized"),
            iv_stale=s.get("iv_stale", False),
        )
        for s in d.get("strikes", [])
    ]
    return CcResult(
        symbol=d["symbol"],
        price=d["price"],
        bb_upper=d["bb_upper"],
        bb_middle=d["bb_middle"],
        bb_lower=d["bb_lower"],
        sma_ratio=d["sma_ratio"],
        rsi=d["rsi"],
        iv_rank=d.get("iv_rank"),
        iv_percentile=d.get("iv_percentile"),
        earnings_date=d.get("earnings_date"),
        earnings_within_dte=d.get("earnings_within_dte", False),
        vol_resistance_126_1=d.get("vol_resistance_126_1"),
        vol_resistance_126_2=d.get("vol_resistance_126_2"),
        vol_resistance_126_3=d.get("vol_resistance_126_3"),
        dte=d["dte"],
        expiration=d["expiration"],
        strikes=strikes,
        best_cc_score=d.get("best_cc_score", 0.0),
        using_hv_fallback=d.get("using_hv_fallback", False),
        expected_move=d.get("expected_move", 0.0),
        dist_from_52w_high_pct=d.get("dist_from_52w_high_pct", 0.0),
        chain_median_oi=d.get("chain_median_oi", 0.0),
    )


def _ditm_from_dict(d: dict[str, Any]) -> DitmResult:
    strikes = [
        DitmStrikeResult(
            strike=s["strike"],
            delta=s["delta"],
            mid=s["mid"],
            extrinsic_pct=s["extrinsic_pct"],
            theta_annualized_pct=s["theta_annualized_pct"],
            breakeven_pct=s["breakeven_pct"],
            capital_efficiency_pct=s["capital_efficiency_pct"],
            bid_ask_spread_pct=s.get("bid_ask_spread_pct"),
            chain_oi=s.get("chain_oi", 0),
            env_score=s["env_score"],
            strike_score=s["strike_score"],
            ditm_score=s["ditm_score"],
            env_detail=s.get("env_detail", ""),
            strike_detail=s.get("strike_detail", ""),
            is_best=s.get("is_best", False),
            iv_fallback=s.get("iv_fallback", False),
            tier=s.get("tier"),
            score_v4=s.get("score_v4"),
            factor_breakdown=s.get("factor_breakdown") or {},
        )
        for s in d.get("strikes", [])
    ]
    return DitmResult(
        symbol=d["symbol"],
        price=d["price"],
        sma_ratio=d["sma_ratio"],
        hv_rank=d["hv_rank"],
        hv30=d["hv30"],
        weekly_rsi=d["weekly_rsi"],
        ret_200d=d["ret_200d"],
        dist_from_52w_high_pct=d["dist_from_52w_high_pct"],
        earnings_date=d.get("earnings_date"),
        days_to_earnings=d.get("days_to_earnings"),
        earnings_within_dte=d.get("earnings_within_dte", False),
        dte=d["dte"],
        expiration=d["expiration"],
        strikes=strikes,
        best_ditm_score=d.get("best_ditm_score", 0.0),
        gap_3d_pct=d.get("gap_3d_pct", 0.0),
        macro_hold=d.get("macro_hold", False),
        chain_median_oi=d.get("chain_median_oi", 0.0),
        iv_percentile=d.get("iv_percentile"),
        trend_r2=d.get("trend_r2"),
        best_tier=d.get("best_tier"),
    )


# ---------------------------------------------------------------------------
# Swing results (ADR-0025)
# ---------------------------------------------------------------------------

def get_swing_results(
    tickers: list[str],
    top_n: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None, float | None]:
    """Return precomputed swing results.

    Returns:
        (rows, regime_dict, last_updated_at, oldest_age_s)
        rows         — qualified SwingResult dicts sorted by swing_score desc, sliced to top_n
        regime_dict  — RegimeState fields extracted from the freshest doc; empty dict if unavailable
        last_updated_at — ISO UTC of the newest doc, or None
        oldest_age_s    — seconds since oldest doc was written, or None
    """
    latest_run_id = _latest_run_id("swing")
    docs = _fetch_docs(
        "swing",
        tickers,
        run_id=latest_run_id,
        allow_empty_filtered=True,
    )
    rows: list[dict[str, Any]] = []
    regime_dict: dict[str, Any] = {}

    for doc in docs:
        result_data = doc.get("result")
        if not result_data:
            continue
        data = result_data.get("data")
        if not data:
            continue
        # Extract regime from first doc that has it.
        if not regime_dict:
            regime_dict = result_data.get("regime") or {}
        rows.append(data)

    rows.sort(
        key=lambda r: max(
            float(r.get("swing_score", 0.0) or 0.0),
            float(r.get("swing_score_v3", 0.0) or 0.0),
        ),
        reverse=True,
    )
    last_updated, oldest_age = _timestamps(docs)
    return rows[:top_n], regime_dict, last_updated, oldest_age
