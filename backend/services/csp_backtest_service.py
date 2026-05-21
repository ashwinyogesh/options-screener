"""
CSP backtest engine — extracted from scripts/backtest_csp.py per ADR-0031 follow-up.

The script wraps this service for CLI use; the router wraps it for per-ticker
on-demand backtests in the UI. Both share the same scoring + price logic so
there is exactly one source of truth.

Public API
----------

    backtest_ticker(symbol: str, years: int = 2, dte: int = 35) -> BacktestResult
        High-level: prepare history, walk forward, score, mark to expiry,
        compute summary stats. Returns one consolidated result object suitable
        for both the API response and the CSV ledger.

    backtest_universe(symbols: list[str], ...) -> pd.DataFrame
        Lower-level: returns the raw per-trade ledger as a DataFrame. Used by
        the CLI script.

The renormalised strike score (Δ + ROC only, no BA/LQ) and HV(30) IV proxy are
preserved exactly as the script used them — see ADR-0031 §"Acknowledged
limitations" for the methodology rationale.
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

from services.greeks_service import black_scholes_put_delta
from services.scoring.env import compute_env_score
from services.scoring.strike import _score_delta_symmetric_methodd, _score_roc_methodd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (sourced from production scoring; do not redefine elsewhere)
# ---------------------------------------------------------------------------

DEFAULT_DTE = 35
DEFAULT_YEARS = 2  # UI default — script overrides to 3
DEFAULT_WEEKLY_STEP = 1
DEFAULT_RF = 0.045
DELTA_GATE = (-0.35, -0.10)        # production filter (csp_service.py)
ITM_TOL = 1.02                     # production strike_filter (2% ITM tolerance)
STRIKE_GRID_FRACTIONS = [0.85, 0.875, 0.90, 0.925, 0.95, 0.975]
STRIKE_QUANT_MAX = 40.0 + 30.0     # v3.4 Method D: Δ(40) + ROC(30); BA+LQ absent in backtest

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
    """One scored, marked-to-expiry CSP trade."""
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
    """Headline stats for one (ticker, years, dte) backtest."""
    n_trades: int
    n_winners: int
    n_losers: int
    n_assigned: int
    win_rate: float                 # fraction of pnl > 0
    assign_rate: float              # fraction with stock put to seller
    mean_roc: float                 # mean realised annualised ROC, %
    median_roc: float
    mean_score: float
    spearman_rho: float             # corr(score, realised ROC)
    spearman_p: float
    monotone_buckets: bool          # True iff bucket means non-decreasing
    cutoff_delta_roc: float         # mean(roc | score>=65) - mean(roc | score<65)
    equity_curve: list[float] = field(default_factory=list)  # cumulative pnl per trade


@dataclass
class BacktestResult:
    """Full per-ticker backtest output for the API."""
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
    "HV(30) used as IV proxy — no historical chain data available; real premium "
    "is typically richer in calm regimes, so realised ROC here is conservative.",
    "Strike-side BA (bid-ask) and LQ (liquidity) factors omitted from scoring "
    "and renormalised to Δ + ROC only. This validates the *signal*, not fillability.",
    "Synthetic strike grid at 0.85–0.975× spot; production hard filters preserved "
    "(delta ∈ [−0.35, −0.10], strike < spot × 1.02).",
    "Weekly walk-forward; one trade per scan date (the max-final-score strike).",
    "No commissions, dividends, early-assignment, or trade-management (rolls / BTC).",
    "Per-ticker sample is small (~50–110 trades for 2–3 years weekly). Treat tile "
    "numbers as directional, not statistically tight.",
)


# ---------------------------------------------------------------------------
# Indicator pre-compute (vectorised once per ticker)
# ---------------------------------------------------------------------------

@dataclass
class TickerSeries:
    """Pre-computed indicator series for one ticker, indexed by date."""
    df: pd.DataFrame


def _prepare_ticker(symbol: str, start: str, end: str) -> Optional[TickerSeries]:
    """Fetch OHLCV and vectorise every per-date scoring input we need."""
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

def _bs_put_price(S: float, K: float, r: float, T: float, sigma: float) -> float:
    """Black-Scholes European put price (matches the delta function's assumptions)."""
    from scipy.stats import norm
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return float(K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _parse_env_detail(detail: str) -> dict[str, float]:
    """Parse 'IVP:25 Tr:15 SMA:5 SLP:3 RSI:20 OI:0' into a dict keyed by factor code."""
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


def _best_csp_trade(
    *,
    ticker: str,
    scan_date: pd.Timestamp,
    series: TickerSeries,
    dte: int,
    rf: float,
) -> Optional[Trade]:
    """Build the strike grid, score each, return the highest-final-score Trade."""
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
        direction="csp",
        sma_ratio=sma_ratio,
        sma50_slope_pct=sma50_slope_pct,
        iv_percentile=iv_pct,
    )
    env_bk = _parse_env_detail(env_detail)

    T = dte / 365.0
    best: Optional[Trade] = None
    for frac in STRIKE_GRID_FRACTIONS:
        strike = round(spot * frac, 2)

        if not (strike < spot * ITM_TOL):
            continue

        delta = black_scholes_put_delta(spot, strike, rf, T, hv30)
        if not (DELTA_GATE[0] <= delta <= DELTA_GATE[1]):
            continue

        premium = _bs_put_price(spot, strike, rf, T, hv30)
        if premium <= 0:
            continue

        p_delta = _score_delta_symmetric_methodd(delta, ideal=-0.225)
        capital_per_share = strike - premium
        if capital_per_share <= 0:
            continue
        roc = (premium / capital_per_share) * (365.0 / dte) * 100.0
        p_roc = _score_roc_methodd(roc)
        strike_quant_score = (p_delta + p_roc) * 100.0 / STRIKE_QUANT_MAX
        final_score = round(0.4 * env_score + 0.6 * strike_quant_score, 1)

        pnl = 100.0 * (premium - max(0.0, strike - spot_at_exp))
        capital = 100.0 * (strike - premium)
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
            assigned=int(spot_at_exp < strike),
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
# Public driver — universe (used by the script)
# ---------------------------------------------------------------------------

def backtest_universe(
    tickers: list[str],
    *,
    years: int = DEFAULT_YEARS,
    dte: int = DEFAULT_DTE,
    weekly_step: int = DEFAULT_WEEKLY_STEP,
    rf: float = DEFAULT_RF,
) -> pd.DataFrame:
    """Walk-forward backtest across a list of tickers. Returns the per-trade ledger."""
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
            trade = _best_csp_trade(
                ticker=t, scan_date=sd, series=series, dte=dte, rf=rf,
            )
            if trade is not None:
                trades.append(trade)

    df = pd.DataFrame([asdict(t) for t in trades])
    logger.info("Generated %d trades across %d scan dates", len(df), len(scan_dates))
    return df


# ---------------------------------------------------------------------------
# Public driver — single ticker (used by the API)
# ---------------------------------------------------------------------------

def backtest_ticker(
    symbol: str,
    *,
    years: int = DEFAULT_YEARS,
    dte: int = DEFAULT_DTE,
    weekly_step: int = DEFAULT_WEEKLY_STEP,
    rf: float = DEFAULT_RF,
) -> BacktestResult:
    """
    Run a per-ticker walk-forward backtest. Raises BacktestError if the ticker
    has insufficient history or no trades pass the production filters.
    """
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
        t = _best_csp_trade(ticker=sym, scan_date=sd, series=series, dte=dte, rf=rf)
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
