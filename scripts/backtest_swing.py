"""
Bar-by-bar backtest harness for the Swing screener — single symbol.

Walks historical daily OHLC from index N onward, runs `classify_setup` +
`build_risk_plan` on each up-to-bar slice (no look-ahead), and simulates each
qualifying signal forward to its stop / target / max-hold exit.

Modelled (v2):
- Regime gate / multiplier (SPY trend + VIX 1y percentile + IWM/SPY RS).
  Breadth is approximated as 50 (neutral) — full universe breadth would require
  fetching ~160 names per bar.
- Earnings hard blocks (≤1d any setup, ≤7d reversion) using yfinance's
  historical earnings dates.

NOT modelled (deliberate scope):
- Universe breadth (defaulted to 50 → neutral).
- Earnings *multiplier* on score (we still report `raw_score`; the live screener
  haircuts it but for a backtest the geometry is what matters — multipliers
  affect ranking, not the trade you take).
- Institutional ownership (snapshot only, not historical).

Outcome model per trade:
- Entry: trigger price on the signal bar (assumed fillable next session).
- Walk forward up to `hold_max_days` bars.
- Stop hit: bar `low <= stop` → exit at stop (R = −1).
- Target hit: bar `high >= target` → exit at target (R = +r_mult).
- Both in same bar: assume stop first (conservative).
- Neither: exit at last bar's close → R = (close − entry) / risk.

Usage:
    cd backend
    .\\venv\\Scripts\\python.exe ..\\scripts\\backtest_swing.py GOOGL --years 3
    .\\venv\\Scripts\\python.exe ..\\scripts\\backtest_swing.py GOOGL --no-regime --no-earnings

Output: per-trade ledger + aggregate stats per setup type.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Make `services.*` imports work when run as `python -m scripts.backtest_swing`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.indicators import compute_rsi  # noqa: E402
from services.swing.classifier import classify_setup  # noqa: E402
from services.swing.indicators import (  # noqa: E402
    compute_ad_line_slope,
    compute_adx,
    compute_atr,
    compute_avg_daily_volume,
    compute_bb_squeeze_percentile,
    compute_consolidation_base,
    compute_ema_alignment,
    compute_fib_retracement_hold,
    compute_gap_fill_candidate,
    compute_higher_lows,
    compute_macd_histogram_inflection,
    compute_rs_vs_spy,
    compute_rsi_divergence,
    compute_stochastic,
    compute_structure_high_reclaim,
    compute_volume_surge,
)
from services.swing.regime import (  # noqa: E402
    REGIME_MULT_MAX,
    REGIME_MULT_MIN,
    RR_GATE_BY_REGIME,
    W_BREADTH,
    W_INDEX,
    W_RISK_APPETITE,
    W_TOTAL,
    W_VOL,
    _classify_index_trend,
    _classify_vix_regime,
    _compute_risk_appetite,
    _ema,
    _label_regime,
    _risk_appetite_score,
    _vix_percentile,
)
from services.swing.risk import build_risk_plan  # noqa: E402

MIN_BARS = 220   # enough for EMA200 + indicators
MIN_SETUP_SCORE = 40.0
DEFAULT_RR_GATE = 2.5  # used when --no-regime


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    setup: str
    setup_score: float
    entry: float
    stop: float
    target: float
    rr_planned: float
    exit_price: float
    exit_reason: str   # "stop" | "target" | "time"
    r_realized: float
    days_held: int
    regime_label: str = ""
    rr_gate: float = 0.0
    regime_mult: float = 1.0
    days_to_earnings: int | None = None


def _fetch(symbol: str, years: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch symbol, SPY, ^VIX, IWM.

    Note: auto_adjust=True (split/dividend-adjusted) is REQUIRED for any
    historical backtest. With auto_adjust=False, pre-split bars print at
    the old nominal price (e.g., pre-2024 NVDA at $1100, post at $110),
    making every entry/exit/ATR/SMA computation discontinuous across
    corporate actions. Matches backend/services/data_service.get_ohlc.
    """
    period = f"{max(years, 1) + 1}y"
    sym_df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    spy = yf.Ticker("SPY").history(period=period, auto_adjust=True)
    vix = yf.Ticker("^VIX").history(period=period, auto_adjust=True)
    iwm = yf.Ticker("IWM").history(period=period, auto_adjust=True)
    if sym_df.empty:
        raise SystemExit(f"No data for {symbol}")
    for d in (sym_df, spy, vix, iwm):
        d.index = pd.to_datetime(d.index).tz_localize(None)
    return sym_df, spy, vix, iwm


def _fetch_earnings_dates(symbol: str) -> list[pd.Timestamp]:
    """Historical + future earnings dates (yfinance returns ~last 4 quarters)."""
    try:
        df = yf.Ticker(symbol).get_earnings_dates(limit=40)
        if df is None or df.empty:
            return []
        idx = pd.to_datetime(df.index).tz_localize(None)
        return sorted(idx.tolist())
    except Exception:  # noqa: BLE001
        return []


def _days_to_next_earnings(bar_date: pd.Timestamp, earnings_dates: list[pd.Timestamp]) -> int | None:
    """Bars-day count to next earnings on/after `bar_date`."""
    if not earnings_dates:
        return None
    for ed in earnings_dates:
        delta = (ed - bar_date).days
        if delta >= 0:
            return delta
    return None


@dataclass
class HistRegime:
    label: str           # risk_on / neutral / risk_off
    rr_gate: float
    multiplier: float
    disable_setups: tuple[str, ...]


def _regime_at(spy_df: pd.DataFrame, vix_df: pd.DataFrame, iwm_df: pd.DataFrame) -> HistRegime:
    """Replicate `services.swing.regime.compute_regime` using up-to-bar slices.

    Breadth is fixed at 50 (neutral) — full universe breadth would require
    fetching ~160 names per bar (out of scope).
    """
    # Index trend
    if len(spy_df) >= 50:
        close = float(spy_df["Close"].iloc[-1])
        ema21 = _ema(spy_df["Close"], 21)
        ema50 = _ema(spy_df["Close"], 50)
        _, index_score = _classify_index_trend(close, ema21, ema50)
    else:
        index_score = 50.0

    # VIX percentile
    if not vix_df.empty:
        vix_val, vix_pct = _vix_percentile(vix_df["Close"])
        _, vol_score = _classify_vix_regime(vix_val, vix_pct)
    else:
        vol_score = 50.0

    # IWM/SPY risk appetite
    if not iwm_df.empty and len(spy_df) >= 21:
        rs = _compute_risk_appetite(iwm_df["Close"], spy_df["Close"])
        ra_score = _risk_appetite_score(rs)
    else:
        ra_score = 50.0

    # Breadth proxy (no universe data available bar-by-bar).
    breadth_score = 50.0

    risk_on_score = (
        W_INDEX * index_score
        + W_VOL * vol_score
        + W_BREADTH * breadth_score
        + W_RISK_APPETITE * ra_score
    ) / W_TOTAL

    label = _label_regime(risk_on_score)
    multiplier = max(REGIME_MULT_MIN, min(REGIME_MULT_MAX,
        REGIME_MULT_MIN + (REGIME_MULT_MAX - REGIME_MULT_MIN) * risk_on_score / 100.0))
    return HistRegime(
        label=label,
        rr_gate=RR_GATE_BY_REGIME[label],
        multiplier=round(multiplier, 3),
        disable_setups=("reversion",) if label == "risk_off" else (),
    )


def _features(df: pd.DataFrame, spy_df: pd.DataFrame) -> dict | None:
    """Replicate process_symbol's feature dict using up-to-bar slices."""
    price = float(df["Close"].iloc[-1])
    atr14 = compute_atr(df, 14)
    if atr14 != atr14 or atr14 <= 0:
        return None
    return {
        "price": price,
        "rsi": compute_rsi(df, 14),
        "stochastic": compute_stochastic(df),
        "ema_alignment": compute_ema_alignment(df),
        "adx": compute_adx(df, 14),
        "bb_squeeze_pct": compute_bb_squeeze_percentile(df),
        "rs_vs_spy": compute_rs_vs_spy(df, spy_df, 20),
        "higher_lows": compute_higher_lows(df),
        "rsi_divergence": compute_rsi_divergence(df),
        "macd_inflection": compute_macd_histogram_inflection(df),
        "consolidation_base": compute_consolidation_base(df),
        "volume_surge": compute_volume_surge(df),
        "fib_618_hold": compute_fib_retracement_hold(df),
        "gap_fill": compute_gap_fill_candidate(df),
        "structure_reclaim": compute_structure_high_reclaim(df),
    }


def _recent_swing_low(df: pd.DataFrame, lookback: int = 10) -> float:
    if len(df) < lookback:
        return 0.0
    return float(df["Low"].iloc[-lookback:].min())


def _simulate(
    entry: float,
    stop: float,
    target: float,
    risk: float,
    fwd: pd.DataFrame,
    hold_max: int,
) -> tuple[str, float, float, int]:
    """Walk forward and return (reason, exit_price, r_realized, days_held)."""
    for i in range(min(hold_max, len(fwd))):
        bar = fwd.iloc[i]
        if bar["Low"] <= stop:
            return "stop", stop, -1.0, i + 1
        if bar["High"] >= target:
            return "target", target, (target - entry) / risk, i + 1
    if len(fwd) == 0:
        return "time", entry, 0.0, 0
    last = fwd.iloc[min(hold_max, len(fwd)) - 1]
    exit_price = float(last["Close"])
    return "time", exit_price, (exit_price - entry) / risk, min(hold_max, len(fwd))


def run_backtest(
    symbol: str,
    years: int = 3,
    cooloff_days: int = 5,
    use_regime: bool = True,
    use_earnings: bool = True,
) -> list[Trade]:
    sym_df, spy_df, vix_df, iwm_df = _fetch(symbol, years)
    earnings_dates = _fetch_earnings_dates(symbol) if use_earnings else []
    trades: list[Trade] = []
    next_eligible_idx = MIN_BARS

    # Counters for diagnostics
    blocks = {"regime_disable": 0, "regime_rr": 0, "earnings_any": 0, "earnings_reversion": 0}

    for i in range(MIN_BARS, len(sym_df) - 1):
        if i < next_eligible_idx:
            continue
        slice_df = sym_df.iloc[: i + 1]
        bar_date = slice_df.index[-1]
        slice_spy = spy_df.loc[: bar_date]
        if len(slice_spy) < 30:
            continue
        slice_vix = vix_df.loc[: bar_date]
        slice_iwm = iwm_df.loc[: bar_date]

        # Gate: ADV + price + history.
        price = float(slice_df["Close"].iloc[-1])
        if price < 5.0:
            continue
        adv = compute_avg_daily_volume(slice_df, 20)
        if (adv != adv) or adv * price < 5_000_000:
            continue

        features = _features(slice_df, slice_spy)
        if features is None:
            continue
        atr14 = compute_atr(slice_df, 14)

        cls = classify_setup(features)
        if cls["best_score"] < MIN_SETUP_SCORE:
            continue

        # Regime layer
        if use_regime:
            regime = _regime_at(slice_spy, slice_vix, slice_iwm)
        else:
            regime = HistRegime(label="risk_on", rr_gate=DEFAULT_RR_GATE, multiplier=1.0, disable_setups=())

        if cls["best_setup"] in regime.disable_setups:
            blocks["regime_disable"] += 1
            continue

        # Earnings layer
        dte = _days_to_next_earnings(bar_date, earnings_dates) if use_earnings else None
        if dte is not None and dte <= 1:
            blocks["earnings_any"] += 1
            continue
        if cls["best_setup"] == "reversion" and dte is not None and dte <= 7:
            blocks["earnings_reversion"] += 1
            continue

        plan = build_risk_plan(
            setup=cls["best_setup"],
            current_price=price,
            atr14=atr14,
            recent_swing_low=_recent_swing_low(slice_df),
            features=features,
        )
        if not plan.passes_gate or plan.rr < regime.rr_gate:
            blocks["regime_rr"] += 1
            continue

        # Hold-window trim if it would cross earnings.
        hold_max = plan.hold_max_days
        if use_earnings and dte is not None and dte < hold_max:
            hold_max = max(1, dte - 1)

        # Forward window (exclusive of signal bar).
        fwd = sym_df.iloc[i + 1 : i + 1 + hold_max]
        reason, exit_price, r_real, days = _simulate(
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            risk=plan.risk_per_share,
            fwd=fwd,
            hold_max=hold_max,
        )
        trades.append(
            Trade(
                entry_date=str(bar_date.date()),
                exit_date=str(fwd.index[days - 1].date()) if days > 0 and len(fwd) >= days else str(bar_date.date()),
                setup=cls["best_setup"],
                setup_score=round(cls["best_score"], 1),
                entry=round(plan.entry, 2),
                stop=round(plan.stop, 2),
                target=round(plan.target, 2),
                rr_planned=round(plan.rr, 2),
                exit_price=round(exit_price, 2),
                exit_reason=reason,
                r_realized=round(r_real, 2),
                days_held=days,
                regime_label=regime.label,
                rr_gate=regime.rr_gate,
                regime_mult=regime.multiplier,
                days_to_earnings=dte,
            )
        )
        next_eligible_idx = i + max(days, 1) + cooloff_days

    # Summarise blocks
    print(f"\nGate blocks: regime-disabled={blocks['regime_disable']} "
          f"regime-RR={blocks['regime_rr']} "
          f"earnings-any={blocks['earnings_any']} "
          f"earnings-reversion={blocks['earnings_reversion']}")

    return trades


def summarise(trades: list[Trade]) -> None:
    if not trades:
        print("No trades.")
        return
    print(f"\n=== TRADE LEDGER ({len(trades)} trades) ===")
    print(
        f"{'date':<12} {'setup':<10} {'score':>5} {'regime':>9} {'gate':>5} {'dte':>4} "
        f"{'entry':>8} {'stop':>8} {'target':>8} {'rr':>5} "
        f"{'exit':>8} {'reason':>7} {'R':>6} {'days':>4}"
    )
    for t in trades:
        dte = "-" if t.days_to_earnings is None else str(t.days_to_earnings)
        print(
            f"{t.entry_date:<12} {t.setup:<10} {t.setup_score:>5.0f} "
            f"{t.regime_label:>9} {t.rr_gate:>5.2f} {dte:>4} "
            f"{t.entry:>8.2f} {t.stop:>8.2f} {t.target:>8.2f} {t.rr_planned:>5.2f} "
            f"{t.exit_price:>8.2f} {t.exit_reason:>7} "
            f"{t.r_realized:>6.2f} {t.days_held:>4}"
        )

    print("\n=== BY SETUP ===")
    by_setup: dict[str, list[Trade]] = {}
    for t in trades:
        by_setup.setdefault(t.setup, []).append(t)
    print(f"{'setup':<10} {'n':>4} {'wins':>5} {'win%':>6} {'avgR':>7} {'medR':>7} {'sumR':>7}")
    for setup, ts in sorted(by_setup.items()):
        n = len(ts)
        wins = sum(1 for t in ts if t.exit_reason == "target")
        avg_r = sum(t.r_realized for t in ts) / n
        med_r = sorted(t.r_realized for t in ts)[n // 2]
        sum_r = sum(t.r_realized for t in ts)
        print(
            f"{setup:<10} {n:>4} {wins:>5} {wins / n * 100:>5.1f}% "
            f"{avg_r:>7.2f} {med_r:>7.2f} {sum_r:>7.2f}"
        )

    print("\n=== BY REGIME ===")
    by_regime: dict[str, list[Trade]] = {}
    for t in trades:
        by_regime.setdefault(t.regime_label or "—", []).append(t)
    print(f"{'regime':<10} {'n':>4} {'wins':>5} {'win%':>6} {'avgR':>7} {'sumR':>7}")
    for regime, ts in sorted(by_regime.items()):
        n = len(ts)
        wins = sum(1 for t in ts if t.exit_reason == "target")
        avg_r = sum(t.r_realized for t in ts) / n
        sum_r = sum(t.r_realized for t in ts)
        print(
            f"{regime:<10} {n:>4} {wins:>5} {wins / n * 100:>5.1f}% "
            f"{avg_r:>7.2f} {sum_r:>7.2f}"
        )

    print("\n=== OVERALL ===")
    n = len(trades)
    wins = sum(1 for t in trades if t.exit_reason == "target")
    losses = sum(1 for t in trades if t.exit_reason == "stop")
    times = sum(1 for t in trades if t.exit_reason == "time")
    avg_r = sum(t.r_realized for t in trades) / n
    sum_r = sum(t.r_realized for t in trades)
    print(f"n={n}  wins(target)={wins}  losses(stop)={losses}  time-exits={times}")
    print(f"win rate (target only): {wins / n * 100:.1f}%")
    print(f"avg R = {avg_r:.2f}   sum R = {sum_r:.2f}")
    print(f"expectancy = {avg_r:.2f}R per trade")


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest the Swing screener for a single symbol.")
    p.add_argument("symbol", help="Ticker, e.g. GOOGL")
    p.add_argument("--years", type=int, default=3, help="History window in years (default: 3)")
    p.add_argument("--cooloff", type=int, default=5, help="Bars to wait between trades (default: 5)")
    p.add_argument("--no-regime", dest="regime", action="store_false", help="Disable regime gate/multiplier")
    p.add_argument("--no-earnings", dest="earnings", action="store_false", help="Disable earnings gate")
    p.set_defaults(regime=True, earnings=True)
    p.add_argument("--csv", type=str, default=None, help="Optional output CSV path")
    args = p.parse_args()

    print(f"Backtest: {args.symbol.upper()}  years={args.years}  "
          f"regime={'on' if args.regime else 'off'}  earnings={'on' if args.earnings else 'off'}")

    trades = run_backtest(
        args.symbol.upper(),
        years=args.years,
        cooloff_days=args.cooloff,
        use_regime=args.regime,
        use_earnings=args.earnings,
    )
    summarise(trades)

    if args.csv:
        pd.DataFrame([asdict(t) for t in trades]).to_csv(args.csv, index=False)
        print(f"\nLedger written to {args.csv}")


if __name__ == "__main__":
    main()
