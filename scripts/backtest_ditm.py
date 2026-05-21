"""
DITM (Deep-In-The-Money long call) backtest — mirrors CC/CSP methodology.

Walk-forward weekly across the universe, synthetic strike grid, BS pricing
with HV(30) as IV proxy. The DITM score is a directional bull-conviction
score, so we measure ρ(score, realised call return) and per-decile lift.

Trade economics
---------------
* Buy 1 deep-ITM call (delta ~ 0.70-0.95)
* Pay BS call price up-front; capital = 100 * entry_price
* At expiration: payoff = max(spot_at_exp - strike, 0)
* PnL per contract = 100 * (payoff - entry_price)
* Realised ROC (annualised %) = pnl / capital * (365 / dte) * 100

Run: backend\\venv\\Scripts\\python.exe scripts\\backtest_ditm.py
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm, spearmanr

# Make backend importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.universe import MOMENTUM_UNIVERSE  # noqa: E402
from services.scoring.ditm import (  # noqa: E402
    compute_ditm_env_score,
    compute_ditm_strike_score,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DTE = 120
YEARS = 3
WEEKLY_STEP = 2  # bi-weekly to keep n manageable at long DTE
RF = 0.045
DELTA_GATE = (0.70, 0.95)
STRIKE_FRACTIONS = [0.80, 0.85, 0.875, 0.90, 0.925, 0.95]
OUT_PATH = ROOT / "ditm_backtest_full.csv"


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def prepare_ticker(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """Fetch OHLC + compute indicators needed by the DITM env scorer."""
    try:
        warmup_start = (
            datetime.fromisoformat(start) - timedelta(days=400)
        ).strftime("%Y-%m-%d")
        df = yf.Ticker(symbol).history(
            start=warmup_start, end=end, auto_adjust=True, actions=False
        )
        if df.empty or len(df) < 260:
            return None
    except Exception:
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
    ret_200d = close / close.shift(200) - 1.0

    high_52w = close.rolling(252, min_periods=20).max()
    dist52w_pct = (close - high_52w) / high_52w * 100.0

    # weekly RSI(14) — Wilder smoothing on weekly closes
    wk = close.resample("W-FRI").last()
    wk_delta = wk.diff()
    wk_gain = wk_delta.clip(lower=0)
    wk_loss = -wk_delta.clip(upper=0)
    wk_avg_gain = wk_gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    wk_avg_loss = wk_loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    wk_rs = wk_avg_gain / wk_avg_loss
    wk_rsi = 100.0 - 100.0 / (1.0 + wk_rs)
    wk_rsi_daily = wk_rsi.reindex(close.index, method="ffill")

    # Trend R²: 50-day OLS regression of log price vs day index
    def _r2(arr: np.ndarray) -> float:
        if np.isnan(arr).any():
            return np.nan
        x = np.arange(len(arr), dtype=float)
        y = np.log(arr)
        if y.std() == 0:
            return np.nan
        b, a = np.polyfit(x, y, 1)
        yhat = a + b * x
        ss_res = ((y - yhat) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else np.nan

    trend_r2 = close.rolling(50).apply(_r2, raw=True)

    out = pd.DataFrame({
        "Close": close,
        "sma50": sma50,
        "sma200": sma200,
        "ret_200d": ret_200d,
        "dist52w": dist52w_pct,
        "wk_rsi": wk_rsi_daily,
        "hv30": hv30,
        "iv_pct": iv_pct,
        "trend_r2": trend_r2,
    })
    out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
    return out


def bs_call(S: float, K: float, r: float, T: float, sigma: float) -> tuple[float, float]:
    """Returns (call_price, delta)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0.0), 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    price = float(S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2))
    return price, float(norm.cdf(d1))


# ---------------------------------------------------------------------------
# Trade selection
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    scan_date: str
    ticker: str
    spot: float
    strike: float
    dte: int
    expiry_date: str
    hv30: float
    iv_pct: float
    wk_rsi: float
    dist52w: float
    ret_200d: float
    trend_r2: float
    delta: float
    entry_price: float
    extrinsic_pct: float
    leverage: float
    env_score: float
    strike_score: float
    final_score: float
    spot_at_exp: float
    payoff: float
    pnl_per_contract: float
    realised_roc_annualised: float
    realised_return_per_dollar: float
    in_the_money_at_exp: int


def best_ditm_trade(
    *, ticker: str, scan_date: pd.Timestamp, df: pd.DataFrame, dte: int, rf: float
) -> Trade | None:
    if scan_date not in df.index:
        idx = df.index[df.index >= scan_date]
        if idx.empty:
            return None
        scan_date = idx[0]
    row = df.loc[scan_date]
    spot = float(row["Close"])
    hv30 = float(row["hv30"]) if not math.isnan(row["hv30"]) else 0.25
    iv_pct = float(row["iv_pct"]) if not math.isnan(row["iv_pct"]) else None
    if spot <= 0 or hv30 <= 0:
        return None

    expiry_target = scan_date + pd.Timedelta(days=dte)
    future_idx = df.index[df.index >= expiry_target]
    if future_idx.empty:
        return None
    expiry_actual = future_idx[0]
    spot_at_exp = float(df.loc[expiry_actual, "Close"])
    realised_dte = (expiry_actual - scan_date).days
    T = realised_dte / 365.0

    sma50 = float(row["sma50"]) if not math.isnan(row["sma50"]) else spot
    sma200 = float(row["sma200"]) if not math.isnan(row["sma200"]) else spot
    price_above_sma50 = spot > sma50
    sma50_above_sma200 = sma50 > sma200
    wk_rsi = float(row["wk_rsi"]) if not math.isnan(row["wk_rsi"]) else float("nan")
    dist52w = float(row["dist52w"]) if not math.isnan(row["dist52w"]) else float("nan")
    ret_200d = float(row["ret_200d"]) if not math.isnan(row["ret_200d"]) else float("nan")
    trend_r2 = float(row["trend_r2"]) if not math.isnan(row["trend_r2"]) else float("nan")

    env_score, _ = compute_ditm_env_score(
        price_above_sma50=price_above_sma50,
        sma50_above_sma200=sma50_above_sma200,
        price=spot,
        sma200=sma200,
        weekly_rsi=wk_rsi,
        dist_from_52w_high_pct=dist52w,
        ret_200d_frac=ret_200d,
        days_to_earnings=None,
        chain_median_oi=500.0,  # neutral synthetic — we don't have chain history
        dte=dte,
        trend_r2=trend_r2,
    )

    best: Trade | None = None
    for frac in STRIKE_FRACTIONS:
        K = round(spot * frac, 2)
        if K <= 0:
            continue
        price, delta = bs_call(spot, K, rf, T, hv30)
        if price <= 0.05:
            continue
        if not (DELTA_GATE[0] <= delta <= DELTA_GATE[1]):
            continue
        intrinsic = max(spot - K, 0.0)
        extrinsic = max(price - intrinsic, 0.0)
        extrinsic_frac = extrinsic / K
        leverage = (delta * spot / price) if price > 0 else 0.0
        strike_score, _ = compute_ditm_strike_score(
            delta=delta,
            strike=K,
            mid=price,
            current_price=spot,
            extrinsic_pct_of_strike_frac=extrinsic_frac,
            bid_ask_spread_pct=None,  # synthetic — skip BA scoring
            iv_percentile=iv_pct,
        )
        final = 0.5 * env_score + 0.5 * strike_score
        if best is not None and final <= best.final_score:
            continue
        payoff = max(spot_at_exp - K, 0.0)
        pnl = 100.0 * (payoff - price)
        capital = 100.0 * price
        realised_roc = (pnl / capital) * (365.0 / realised_dte) * 100.0 if capital > 0 else 0.0
        return_per_dollar = (pnl / capital) if capital > 0 else 0.0
        best = Trade(
            scan_date=scan_date.strftime("%Y-%m-%d"),
            ticker=ticker,
            spot=round(spot, 2),
            strike=K,
            dte=realised_dte,
            expiry_date=expiry_actual.strftime("%Y-%m-%d"),
            hv30=round(hv30, 4),
            iv_pct=round(iv_pct, 1) if iv_pct is not None else float("nan"),
            wk_rsi=round(wk_rsi, 1) if not math.isnan(wk_rsi) else float("nan"),
            dist52w=round(dist52w, 2) if not math.isnan(dist52w) else float("nan"),
            ret_200d=round(ret_200d, 4) if not math.isnan(ret_200d) else float("nan"),
            trend_r2=round(trend_r2, 3) if not math.isnan(trend_r2) else float("nan"),
            delta=round(delta, 3),
            entry_price=round(price, 2),
            extrinsic_pct=round(extrinsic_frac * 100, 3),
            leverage=round(leverage, 2),
            env_score=round(env_score, 1),
            strike_score=round(strike_score, 1),
            final_score=round(final, 2),
            spot_at_exp=round(spot_at_exp, 2),
            payoff=round(payoff, 2),
            pnl_per_contract=round(pnl, 2),
            realised_roc_annualised=round(realised_roc, 2),
            realised_return_per_dollar=round(return_per_dollar, 4),
            in_the_money_at_exp=int(spot_at_exp > K),
        )
    return best


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def backtest_universe(symbols: list[str], years: int, dte: int) -> pd.DataFrame:
    end = datetime.now().date().isoformat()
    start = (datetime.now().date() - timedelta(days=365 * years)).isoformat()
    all_trades: list[Trade] = []
    n = len(symbols)
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        df = prepare_ticker(sym, start, end)
        if df is None:
            print(f"[{i:3}/{n}] {sym}: skipped (insufficient history)")
            continue
        scan_dates = pd.date_range(start=start, end=end, freq=f"{WEEKLY_STEP}W-MON")
        scan_dates = [d for d in scan_dates if d in df.index or any(df.index >= d)]
        sym_trades = 0
        for d in scan_dates:
            t = best_ditm_trade(ticker=sym, scan_date=pd.Timestamp(d), df=df, dte=dte, rf=RF)
            if t is not None:
                all_trades.append(t)
                sym_trades += 1
        print(f"[{i:3}/{n}] {sym}: {sym_trades} trades  (elapsed {time.time()-t0:.0f}s)")
    return pd.DataFrame([asdict(t) for t in all_trades])


def main() -> None:
    print(f"DITM backtest — universe={len(MOMENTUM_UNIVERSE)} tickers, "
          f"years={YEARS}, dte={DTE}")
    df = backtest_universe(MOMENTUM_UNIVERSE, YEARS, DTE)
    print(f"\nTotal trades: {len(df)}")
    if df.empty:
        return
    df.to_csv(OUT_PATH, index=False)
    print(f"Saved -> {OUT_PATH}")

    # Quick decile analysis
    df = df.dropna(subset=["final_score", "realised_roc_annualised"])
    df["decile"] = pd.qcut(df["final_score"], 10, labels=[f"D{i+1}" for i in range(10)])
    g = df.groupby("decile", observed=True).agg(
        n=("final_score", "size"),
        score_min=("final_score", "min"),
        score_max=("final_score", "max"),
        mean_ROC=("realised_roc_annualised", "mean"),
        median_ROC=("realised_roc_annualised", "median"),
        win_pct=("pnl_per_contract", lambda x: 100 * (x > 0).mean()),
        itm_pct=("in_the_money_at_exp", lambda x: 100 * x.mean()),
        mean_pnl=("pnl_per_contract", "mean"),
        mean_leverage=("leverage", "mean"),
    ).round(2)
    print("\n=== DITM decile breakdown ===")
    print(g.to_string())
    rho, p = spearmanr(df["final_score"], df["realised_roc_annualised"])
    print(f"\nSpearman rho(score, realised_roc) = {rho:+.3f}  (p={p:.4f}, n={len(df)})")
    rho2, p2 = spearmanr(df["final_score"], df["pnl_per_contract"])
    print(f"Spearman rho(score, pnl_per_contract) = {rho2:+.3f}  (p={p2:.4f})")


if __name__ == "__main__":
    main()
