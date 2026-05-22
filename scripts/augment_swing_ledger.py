"""Augment swing_backtest_universe.csv with as-of features pulled from yfinance.

For every (symbol, entry_date) row in the swing ledger we compute strict
no-look-ahead features using bars up to and including entry_date:

Per-symbol (from OHLC):
  rsi14, macd_hist, atr_pct, vol20, bb_pos,
  dist_sma20, dist_sma50, dist_sma200,
  pct_off_52w_high, pct_above_52w_low,
  ret_1m, ret_3m, ret_6m,
  vol_surge_20, obv_slope_20,
  base_depth, base_length, gap_up, inside_bar, nr7

Market context (from SPY & ^VIX):
  spy_slope_50, spy_ret_5d, vix_level, vix_vs_med20

Relative strength:
  rs_vs_spy_3m

Writes swing_backtest_universe_enriched.csv.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "swing_backtest_universe.csv"
OUT = ROOT / "swing_backtest_universe_enriched.csv"

# -------- price helpers ----------------------------------------------------


def _download(symbol: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(
        symbol,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd_hist(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd - signal


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff()).fillna(0)
    return (direction * df["Volume"]).cumsum()


# -------- per-row feature extraction --------------------------------------


def features_for_row(
    df: pd.DataFrame, entry_date: pd.Timestamp, spy: pd.DataFrame, vix: pd.Series
) -> dict[str, float]:
    """Compute features using bars up to and INCLUDING entry_date."""
    bars = df.loc[df.index <= entry_date]
    if len(bars) < 220:
        return {}

    close = bars["Close"]
    high = bars["High"]
    low = bars["Low"]
    vol = bars["Volume"]

    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    std20 = close.rolling(20).std().iloc[-1]
    px = float(close.iloc[-1])

    high_52w = high.iloc[-252:].max() if len(bars) >= 252 else high.max()
    low_52w = low.iloc[-252:].min() if len(bars) >= 252 else low.min()

    rsi14 = float(_rsi(close).iloc[-1])
    macd_hist = float(_macd_hist(close).iloc[-1])
    atr_pct = float(_atr(bars).iloc[-1] / px * 100.0)

    log_ret = np.log(close / close.shift(1))
    vol20 = float(log_ret.rolling(20).std().iloc[-1] * np.sqrt(252) * 100.0)

    bb_pos = float((px - sma20) / (2 * std20)) if std20 and not np.isnan(std20) else np.nan

    obv = _obv(bars)
    obv_slope_20 = (
        float((obv.iloc[-1] - obv.iloc[-21]) / abs(obv.iloc[-21]) * 100.0)
        if len(obv) > 21 and obv.iloc[-21] != 0
        else np.nan
    )

    vol_surge_20 = float(vol.iloc[-1] / vol.iloc[-21:-1].mean()) if len(vol) > 21 else np.nan

    # Base structure: 20-day prior swing
    win20 = bars.iloc[-21:-1]
    base_max = float(win20["High"].max()) if len(win20) else np.nan
    base_min_idx = int(win20["Low"].idxmin().toordinal()) if len(win20) else 0
    base_depth = (base_max - px) / base_max * 100.0 if base_max else np.nan
    base_length = (entry_date.toordinal() - base_min_idx) if base_min_idx else np.nan

    gap_up = 1 if len(bars) >= 2 and bars["Open"].iloc[-1] > bars["Close"].iloc[-2] else 0
    prev_h, prev_l = (
        (bars["High"].iloc[-2], bars["Low"].iloc[-2]) if len(bars) >= 2 else (np.nan, np.nan)
    )
    inside_bar = (
        1 if high.iloc[-1] < prev_h and low.iloc[-1] > prev_l else 0
    ) if not np.isnan(prev_h) else 0
    nr7 = 1 if len(bars) >= 7 and (high.iloc[-1] - low.iloc[-1]) < (high.iloc[-7:-1] - low.iloc[-7:-1]).min() else 0

    # Returns
    def _ret(n: int) -> float:
        if len(close) <= n:
            return np.nan
        return float((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100.0)

    ret_1m, ret_3m, ret_6m = _ret(21), _ret(63), _ret(126)

    # Market context (as-of)
    spy_bars = spy.loc[spy.index <= entry_date]
    if len(spy_bars) >= 51:
        spy_close = spy_bars["Close"]
        spy_slope_50 = float(
            (spy_close.iloc[-1] / spy_close.iloc[-51] - 1) * 100.0 / 50.0
        )
        spy_ret_5d = float((spy_close.iloc[-1] / spy_close.iloc[-6] - 1) * 100.0)
        # RS vs SPY 3m
        if len(spy_close) >= 64 and len(close) >= 64:
            sym_ret = (close.iloc[-1] / close.iloc[-64] - 1) * 100.0
            spy_ret = (spy_close.iloc[-1] / spy_close.iloc[-64] - 1) * 100.0
            rs_vs_spy_3m = float(sym_ret - spy_ret)
        else:
            rs_vs_spy_3m = np.nan
    else:
        spy_slope_50 = spy_ret_5d = rs_vs_spy_3m = np.nan

    vix_bars = vix.loc[vix.index <= entry_date]
    if len(vix_bars) >= 21:
        vix_level = float(vix_bars.iloc[-1])
        vix_vs_med20 = float(vix_level / vix_bars.iloc[-21:-1].median() - 1) * 100.0
    else:
        vix_level = vix_vs_med20 = np.nan

    return {
        "rsi14": rsi14,
        "macd_hist": macd_hist,
        "atr_pct": atr_pct,
        "vol20": vol20,
        "bb_pos": bb_pos,
        "dist_sma20": (px - sma20) / sma20 * 100.0 if sma20 else np.nan,
        "dist_sma50": (px - sma50) / sma50 * 100.0 if sma50 else np.nan,
        "dist_sma200": (px - sma200) / sma200 * 100.0 if sma200 else np.nan,
        "pct_off_52w_high": (px / high_52w - 1) * 100.0 if high_52w else np.nan,
        "pct_above_52w_low": (px / low_52w - 1) * 100.0 if low_52w else np.nan,
        "ret_1m": ret_1m,
        "ret_3m": ret_3m,
        "ret_6m": ret_6m,
        "vol_surge_20": vol_surge_20,
        "obv_slope_20": obv_slope_20,
        "base_depth": base_depth,
        "base_length": base_length,
        "gap_up": gap_up,
        "inside_bar": inside_bar,
        "nr7": nr7,
        "spy_slope_50": spy_slope_50,
        "spy_ret_5d": spy_ret_5d,
        "vix_level": vix_level,
        "vix_vs_med20": vix_vs_med20,
        "rs_vs_spy_3m": rs_vs_spy_3m,
        "log_price": float(np.log(px)),
    }


# -------- orchestration ----------------------------------------------------


def _process_symbol(
    symbol: str,
    rows: pd.DataFrame,
    start: str,
    end: str,
    spy: pd.DataFrame,
    vix: pd.Series,
) -> list[dict]:
    df = _download(symbol, start, end)
    if df.empty:
        print(f"  [{symbol}] no bars", flush=True)
        return []
    out = []
    for _, r in rows.iterrows():
        feats = features_for_row(df, pd.Timestamp(r["entry_date"]), spy, vix)
        if not feats:
            continue
        feats["symbol"] = symbol
        feats["entry_date"] = r["entry_date"]
        out.append(feats)
    return out


def main() -> int:
    led = pd.read_csv(LEDGER, parse_dates=["entry_date", "exit_date"])
    led["entry_date"] = led["entry_date"].dt.tz_localize(None).dt.normalize()
    print(f"Ledger: {len(led)} trades, {led['symbol'].nunique()} symbols")

    start = (led["entry_date"].min() - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
    end = (led["entry_date"].max() + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    print(f"Bar range: {start} → {end}")

    print("Downloading SPY & VIX ...", flush=True)
    spy = _download("SPY", start, end)
    vix_df = _download("^VIX", start, end)
    vix = vix_df["Close"] if not vix_df.empty else pd.Series(dtype=float)
    print(f"  SPY bars={len(spy)}  VIX bars={len(vix)}")

    groups = list(led.groupby("symbol"))
    print(f"Augmenting features for {len(groups)} symbols (parallel) ...", flush=True)

    all_feats: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {
            ex.submit(_process_symbol, sym, rows, start, end, spy, vix): sym
            for sym, rows in groups
        }
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                rows_out = fut.result()
                all_feats.extend(rows_out)
            except Exception as e:
                print(f"  [{sym}] ERROR {e}", flush=True)
            done += 1
            if done % 20 == 0:
                print(f"  ...{done}/{len(groups)} symbols done", flush=True)

    feats_df = pd.DataFrame(all_feats)
    feats_df["entry_date"] = pd.to_datetime(feats_df["entry_date"]).dt.normalize()
    print(f"Computed feature rows: {len(feats_df)}")

    merged = led.merge(feats_df, on=["symbol", "entry_date"], how="left")
    print(f"Merged: {len(merged)} (lost {len(led) - len(merged)} on merge)")

    # Coverage report
    new_cols = [c for c in feats_df.columns if c not in {"symbol", "entry_date"}]
    print("\nFeature coverage:")
    for c in new_cols:
        nn = merged[c].notna().sum()
        print(f"  {c:22s}  {nn}/{len(merged)}  ({nn/len(merged)*100:.1f}%)")

    merged.to_csv(OUT, index=False)
    print(f"\nWrote → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
