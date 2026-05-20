"""
CC (Covered Call) backtest engine — mirror of csp_backtest_service for the
short-call side. Same walk-forward / HV(30)-as-IV-proxy methodology.

Public API
----------

    backtest_ticker(symbol: str, years: int = 2, dte: int = 35) -> BacktestResult
        Per-ticker driver used by the API.

    backtest_universe(symbols: list[str], ...) -> pd.DataFrame
        CLI/notebook driver returning the raw per-trade ledger.

Trade economics
---------------

We simulate buying 100 shares and immediately writing one OTM call:

* capital deployed per contract = 100 × (spot − credit)   (matches scoring basis)
* at expiration:
    - if spot_at_exp ≥ strike : called away  → pnl = 100 × ((strike − spot) + credit)
    - if spot_at_exp <  strike : not called  → pnl = 100 × ((spot_at_exp − spot) + credit)
* realised annualised ROC = pnl / capital × (365 / dte) × 100

This is the *total* covered-call P&L (stock + short call), which is what the CC
score is designed to optimise — not premium-only yield.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from services.greeks_service import black_scholes_call_delta
from services.scoring.env import compute_env_score
from services.scoring.strike import _score_delta_symmetric, _score_roc

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DTE = 35
DEFAULT_YEARS = 2          # UI default — script overrides to 3
DEFAULT_WEEKLY_STEP = 1
DEFAULT_RF = 0.045
DELTA_GATE = (0.10, 0.35)                              # production cc filter (mirrored)
OTM_TOL = 0.98                                         # require strike > spot × 0.98
STRIKE_GRID_FRACTIONS = [1.025, 1.05, 1.075, 1.10, 1.125, 1.15]
STRIKE_QUANT_MAX = 25.0 + 35.0                         # Δ(25) + ROC(35) — no BA/LQ

SCORE_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 50.0, "0-50"),
    (50.0, 65.0, "50-65"),
    (65.0, 75.0, "65-75"),
    (75.0, 85.0, "75-85"),
    (85.0, 100.1, "85-100"),
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BacktestError(Exception):
    """Backtest could not be produced for the given symbol."""

    def __init__(self, symbol: str, reason: str) -> None:
        super().__init__(f"{symbol}: {reason}")
        self.symbol = symbol
        self.reason = reason


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    """One scored, marked-to-expiry CC trade."""
    scan_date: str
    ticker: str
    spot: float
    strike: float
    dte: int
    expiry_date: str
    hv30: float
    iv_pct: float
    rsi: float
    sma_ratio: float
    sma50_slope_pct: float
    dist52w: float
    delta: float
    premium: float
    env_IVP: float
    env_Tr: float
    env_SMA: float
    env_SLP: float
    env_RSI: float
    env_OI: float
    strike_Delta: float
    strike_ROC: float
    env_score: float
    strike_quant_score: float
    final_score: float
    spot_at_exp: float
    assigned: int
    pnl_per_contract: float
    realised_roc_annualised: float
    realised_return_per_dollar: float


@dataclass
class BucketStat:
    bucket: str
    n: int
    mean_roc: float
    median_roc: float
    win_rate: float
    assign_rate: float


@dataclass
class BacktestSummary:
    n_trades: int
    n_winners: int
    n_losers: int
    n_assigned: int
    win_rate: float
    assign_rate: float                # fraction with stock called away
    mean_roc: float
    median_roc: float
    mean_score: float
    spearman_rho: float
    spearman_p: float
    monotone_buckets: bool
    cutoff_delta_roc: float
    equity_curve: list[float] = field(default_factory=list)


@dataclass
class BacktestResult:
    symbol: str
    years: int
    dte: int
    scan_start: str
    scan_end: str
    summary: BacktestSummary
    buckets: list[BucketStat]
    trades: list[Trade]
    caveats: list[str] = field(default_factory=lambda: list(BACKTEST_CAVEATS))


BACKTEST_CAVEATS: tuple[str, ...] = (
    "HV(30) used as IV proxy — no historical chain data available; real CC "
    "premium is typically richer in calm regimes, so realised ROC here is "
    "conservative.",
    "Strike-side BA (bid-ask) and LQ (liquidity) factors omitted from scoring "
    "and renormalised to Δ + ROC only. This validates the *signal*, not fillability.",
    "Synthetic strike grid at 1.025–1.15× spot (OTM calls); production hard "
    "filters preserved (delta ∈ [+0.10, +0.35], strike > spot × 0.98).",
    "Weekly walk-forward; one trade per scan date (the max-final-score strike).",
    "Total CC P&L = (stock change + premium), capped on the upside if called "
    "away. No dividends, commissions, early-assignment, or trade-management (rolls).",
    "Per-ticker sample is small (~50–110 trades for 2–3 years weekly). Treat tile "
    "numbers as directional, not statistically tight.",
)


# ---------------------------------------------------------------------------
# Indicator pre-compute (identical to CSP — env score uses same inputs)
# ---------------------------------------------------------------------------

@dataclass
class TickerSeries:
    df: pd.DataFrame


def _prepare_ticker(symbol: str, start: str, end: str) -> Optional[TickerSeries]:
    try:
        warmup_start = (datetime.fromisoformat(start) - timedelta(days=400)).strftime("%Y-%m-%d")
        df = yf.Ticker(symbol).history(
            start=warmup_start, end=end, auto_adjust=True, actions=False
        )
        if df.empty or len(df) < 260:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch failed for %s: %s", symbol, exc)
        return None

    close = df["Close"]
    log_ret = np.log(close / close.shift(1))
    hv30 = log_ret.rolling(30).std(ddof=1) * np.sqrt(252)

    def _iv_pct_window(window: np.ndarray) -> float:
        today = window[-1]
        if np.isnan(today):
            return np.nan
        finite = window[~np.isnan(window)]
        if len(finite) < 60:
            return np.nan
        return float((finite < today).sum()) / len(finite) * 100.0

    iv_pct = hv30.rolling(252, min_periods=60).apply(_iv_pct_window, raw=True)

    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    sma_ratio = sma50 / sma200
    sma50_slope_pct = (sma50 / sma50.shift(10) - 1.0) * 100.0

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)

    high_52w = close.rolling(252, min_periods=20).max()
    dist52w = (close - high_52w) / high_52w * 100.0

    out = pd.DataFrame({
        "Close": close,
        "sma_ratio": sma_ratio,
        "sma50_slope_pct": sma50_slope_pct,
        "rsi": rsi,
        "dist52w": dist52w,
        "hv30": hv30,
        "iv_pct": iv_pct,
    })
    out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
    return TickerSeries(df=out)


# ---------------------------------------------------------------------------
# Pricing + scoring per (date, ticker)
# ---------------------------------------------------------------------------

def _bs_call_price(S: float, K: float, r: float, T: float, sigma: float) -> float:
    """Black-Scholes European call price."""
    from scipy.stats import norm
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return float(S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _parse_env_detail(detail: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for token in detail.split():
        if ":" not in token:
            continue
        k, v = token.split(":", 1)
        try:
            out[k] = float(v)
        except ValueError:
            continue
    return out


def _best_cc_trade(
    *,
    ticker: str,
    scan_date: pd.Timestamp,
    series: TickerSeries,
    dte: int,
    rf: float,
) -> Optional[Trade]:
    if scan_date not in series.df.index:
        idx = series.df.index
        future = idx[idx >= scan_date]
        if future.empty:
            return None
        scan_date = future[0]

    row = series.df.loc[scan_date]
    spot = float(row["Close"])
    hv30 = float(row["hv30"]) if not math.isnan(row["hv30"]) else 0.25
    iv_pct = float(row["iv_pct"]) if not math.isnan(row["iv_pct"]) else None
    rsi = float(row["rsi"]) if not math.isnan(row["rsi"]) else float("nan")
    dist52w = float(row["dist52w"]) if not math.isnan(row["dist52w"]) else float("nan")
    sma_ratio = float(row["sma_ratio"]) if not math.isnan(row["sma_ratio"]) else 1.0
    sma50_slope_pct = float(row["sma50_slope_pct"]) if not math.isnan(row["sma50_slope_pct"]) else 0.0

    if spot <= 0 or hv30 <= 0:
        return None

    expiry_target = scan_date + pd.Timedelta(days=dte)
    future_idx = series.df.index[series.df.index >= expiry_target]
    if future_idx.empty:
        return None
    expiry_actual = future_idx[0]
    spot_at_exp = float(series.df.loc[expiry_actual, "Close"])
    realised_dte = (expiry_actual - scan_date).days

    env_score, env_detail = compute_env_score(
        iv_rank=None, iv_hv_ratio=None,
        price_above_sma50=False, sma50_above_sma200=False,
        dist_from_52w_high_pct=dist52w,
        rsi=rsi,
        chain_median_oi=0.0,
        earnings_within_dte=False,
        direction="cc",
        sma_ratio=sma_ratio,
        sma50_slope_pct=sma50_slope_pct,
        iv_percentile=iv_pct,
    )
    env_bk = _parse_env_detail(env_detail)

    T = dte / 365.0
    best: Optional[Trade] = None
    for frac in STRIKE_GRID_FRACTIONS:
        strike = round(spot * frac, 2)

        # Production CC filter: strike must be sufficiently OTM
        if not (strike > spot * OTM_TOL):
            continue

        delta = black_scholes_call_delta(spot, strike, rf, T, hv30)
        if not (DELTA_GATE[0] <= delta <= DELTA_GATE[1]):
            continue

        premium = _bs_call_price(spot, strike, rf, T, hv30)
        if premium <= 0:
            continue

        p_delta = _score_delta_symmetric(delta, ideal=+0.225)
        # ROC basis for CC = spot − credit (stock held minus premium collected)
        capital_per_share = spot - premium
        if capital_per_share <= 0:
            continue
        roc = (premium / capital_per_share) * (365.0 / dte) * 100.0
        p_roc = _score_roc(roc)
        strike_quant_score = (p_delta + p_roc) * 100.0 / STRIKE_QUANT_MAX
        final_score = round(0.4 * env_score + 0.6 * strike_quant_score, 1)

        # CC P&L at expiration (per contract = 100 shares)
        assigned = spot_at_exp >= strike
        if assigned:
            pnl = 100.0 * ((strike - spot) + premium)
        else:
            pnl = 100.0 * ((spot_at_exp - spot) + premium)
        capital = 100.0 * (spot - premium)
        realised_roc_ann = pnl / capital * (365.0 / realised_dte) * 100.0
        realised_per_dollar = pnl / capital

        candidate = Trade(
            scan_date=scan_date.strftime("%Y-%m-%d"),
            ticker=ticker,
            spot=round(spot, 2),
            strike=strike,
            dte=realised_dte,
            expiry_date=expiry_actual.strftime("%Y-%m-%d"),
            hv30=round(hv30, 4),
            iv_pct=round(iv_pct, 1) if iv_pct is not None else float("nan"),
            rsi=round(rsi, 1) if not math.isnan(rsi) else float("nan"),
            sma_ratio=round(sma_ratio, 4),
            sma50_slope_pct=round(sma50_slope_pct, 3),
            dist52w=round(dist52w, 2) if not math.isnan(dist52w) else float("nan"),
            delta=delta,
            premium=round(premium, 3),
            env_IVP=env_bk.get("IVP", 0.0),
            env_Tr=env_bk.get("Tr", 0.0),
            env_SMA=env_bk.get("SMA", 0.0),
            env_SLP=env_bk.get("SLP", 0.0),
            env_RSI=env_bk.get("RSI", 0.0),
            env_OI=env_bk.get("OI", 0.0),
            strike_Delta=round(p_delta, 1),
            strike_ROC=round(p_roc, 1),
            env_score=env_score,
            strike_quant_score=round(strike_quant_score, 1),
            final_score=final_score,
            spot_at_exp=round(spot_at_exp, 2),
            assigned=int(assigned),
            pnl_per_contract=round(pnl, 2),
            realised_roc_annualised=round(realised_roc_ann, 2),
            realised_return_per_dollar=round(realised_per_dollar, 4),
        )
        if best is None or candidate.final_score > best.final_score:
            best = candidate

    return best


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def _bucket_stats(df: pd.DataFrame) -> list[BucketStat]:
    rows: list[BucketStat] = []
    for lo, hi, label in SCORE_BUCKETS:
        sub = df[(df["final_score"] >= lo) & (df["final_score"] < hi)]
        if sub.empty:
            rows.append(BucketStat(label, 0, 0.0, 0.0, 0.0, 0.0))
            continue
        roc = sub["realised_roc_annualised"]
        pnl = sub["pnl_per_contract"]
        rows.append(BucketStat(
            bucket=label,
            n=len(sub),
            mean_roc=round(float(roc.mean()), 1),
            median_roc=round(float(roc.median()), 1),
            win_rate=round(float((pnl > 0).mean() * 100), 1),
            assign_rate=round(float(sub["assigned"].mean() * 100), 1),
        ))
    return rows


def _build_summary(df: pd.DataFrame, buckets: list[BucketStat]) -> BacktestSummary:
    from scipy.stats import spearmanr

    pnl = df["pnl_per_contract"]
    roc = df["realised_roc_annualised"]

    above = df[df["final_score"] >= 65]
    below = df[df["final_score"] < 65]
    cutoff_delta = 0.0
    if not above.empty and not below.empty:
        cutoff_delta = float(above["realised_roc_annualised"].mean()
                             - below["realised_roc_annualised"].mean())

    populated = [b.mean_roc for b in buckets if b.n > 0]
    monotone = all(populated[i] <= populated[i + 1] for i in range(len(populated) - 1))

    if len(df) > 2 and roc.std(ddof=1) > 0 and df["final_score"].std(ddof=1) > 0:
        rho, p = spearmanr(df["final_score"], roc)
        rho_v = float(rho) if not (isinstance(rho, float) and math.isnan(rho)) else 0.0
        p_v = float(p) if not (isinstance(p, float) and math.isnan(p)) else 1.0
    else:
        rho_v, p_v = 0.0, 1.0

    return BacktestSummary(
        n_trades=len(df),
        n_winners=int((pnl > 0).sum()),
        n_losers=int((pnl <= 0).sum()),
        n_assigned=int(df["assigned"].sum()),
        win_rate=round(float((pnl > 0).mean() * 100), 1),
        assign_rate=round(float(df["assigned"].mean() * 100), 1),
        mean_roc=round(float(roc.mean()), 1),
        median_roc=round(float(roc.median()), 1),
        mean_score=round(float(df["final_score"].mean()), 1),
        spearman_rho=round(rho_v, 3),
        spearman_p=round(p_v, 4),
        monotone_buckets=monotone,
        cutoff_delta_roc=round(cutoff_delta, 1),
        equity_curve=[round(float(v), 2) for v in pnl.cumsum().tolist()],
    )


# ---------------------------------------------------------------------------
# Public drivers
# ---------------------------------------------------------------------------

def backtest_universe(
    tickers: list[str],
    *,
    years: int = DEFAULT_YEARS,
    dte: int = DEFAULT_DTE,
    weekly_step: int = DEFAULT_WEEKLY_STEP,
    rf: float = DEFAULT_RF,
) -> pd.DataFrame:
    end = datetime.now().date()
    start = end - timedelta(days=365 * years + dte + 30)

    logger.info("Fetching %d tickers from %s to %s", len(tickers), start, end)
    cache: dict[str, TickerSeries] = {}
    for i, t in enumerate(tickers, 1):
        s = _prepare_ticker(t, start.isoformat(), end.isoformat())
        if s is not None:
            cache[t] = s
        if i % 10 == 0:
            logger.info("  prepared %d / %d", i, len(tickers))
    logger.info("Prepared %d / %d tickers (skipped %d for thin history)",
                len(cache), len(tickers), len(tickers) - len(cache))

    scan_start = pd.Timestamp(start) + pd.Timedelta(days=300)
    scan_end = pd.Timestamp(end) - pd.Timedelta(days=dte + 5)
    scan_dates = pd.date_range(scan_start, scan_end, freq=f"{weekly_step}W-MON")

    trades: list[Trade] = []
    for sd in scan_dates:
        for t, series in cache.items():
            trade = _best_cc_trade(
                ticker=t, scan_date=sd, series=series, dte=dte, rf=rf,
            )
            if trade is not None:
                trades.append(trade)

    df = pd.DataFrame([asdict(t) for t in trades])
    logger.info("Generated %d trades across %d scan dates", len(df), len(scan_dates))
    return df


def backtest_ticker(
    symbol: str,
    *,
    years: int = DEFAULT_YEARS,
    dte: int = DEFAULT_DTE,
    weekly_step: int = DEFAULT_WEEKLY_STEP,
    rf: float = DEFAULT_RF,
) -> BacktestResult:
    sym = symbol.strip().upper()
    if not sym or len(sym) > 10 or not sym.isalnum():
        raise BacktestError(symbol, "invalid symbol")

    end = datetime.now().date()
    start = end - timedelta(days=365 * years + dte + 30)

    series = _prepare_ticker(sym, start.isoformat(), end.isoformat())
    if series is None:
        raise BacktestError(sym, "insufficient price history (need ≥ 260 trading days)")

    scan_start = pd.Timestamp(start) + pd.Timedelta(days=300)
    scan_end = pd.Timestamp(end) - pd.Timedelta(days=dte + 5)
    scan_dates = pd.date_range(scan_start, scan_end, freq=f"{weekly_step}W-MON")

    trades: list[Trade] = []
    for sd in scan_dates:
        t = _best_cc_trade(ticker=sym, scan_date=sd, series=series, dte=dte, rf=rf)
        if t is not None:
            trades.append(t)

    if not trades:
        raise BacktestError(sym, "no trades passed production hard filters in the window")

    df = pd.DataFrame([asdict(t) for t in trades])
    buckets = _bucket_stats(df)
    summary = _build_summary(df, buckets)

    return BacktestResult(
        symbol=sym,
        years=years,
        dte=dte,
        scan_start=str(scan_dates[0].date()) if len(scan_dates) else "",
        scan_end=str(scan_dates[-1].date()) if len(scan_dates) else "",
        summary=summary,
        buckets=buckets,
        trades=trades,
    )
