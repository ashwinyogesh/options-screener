"""
Universe-wide Swing backtest + scoring-contribution IC/Spearman analysis.

Walks daily OHLC bar-by-bar for every ticker in `SWING_UNIVERSE` (≈158 names),
runs the same gate / classifier / risk planner / regime / earnings stack the
live `swing_service` uses, and for every qualifying signal:

  1. Simulates the trade forward (stop / target / time exit) → `r_realized`.
  2. Calls `compute_swing_score(...)` and records every contribution
     (rr_pts, setup_pts, ctx_pts, inst_pts, raw, multipliers, final).

After the universe sweep finishes it produces an IC report:

  * Per-component Spearman rho (and p-value) vs `r_realized`.
  * Decile/quintile bucket analysis of `final_score` vs realised R.
  * Per-setup breakdown so the contributions are interpretable.

This is a *scoring diagnostic*, not a strategy P&L: it answers
"does the screener's score rank trades correctly?" — the same question we
answered for CSP / CC / DITM with their `_factor_correlation` scripts.

Usage (from repo root):

    backend\\venv\\Scripts\\python.exe scripts\\backtest_swing_universe.py ^
        --years 3 --workers 8 --csv swing_backtest_universe.csv

Notes:
- Breadth proxy in the historical regime stays at 50 (neutral); see
  `scripts/backtest_swing.py` for the rationale.
- Institutional-ownership is a single yfinance snapshot (no historical
  series available), the same compromise the live service makes.
- Indicators per bar dominate runtime; use --workers to parallelise across
  tickers. ThreadPool works because pandas/numpy releases the GIL.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Reuse the single-symbol helpers from the existing backtester.
from backtest_swing import (  # noqa: E402
    DEFAULT_RR_GATE,
    MIN_BARS,
    MIN_SETUP_SCORE,
    HistRegime,
    _days_to_next_earnings,
    _features,
    _recent_swing_low,
    _regime_at,
    _simulate,
)
from services.swing.classifier import classify_setup  # noqa: E402
from services.swing.indicators import (  # noqa: E402
    compute_ad_line_slope,
    compute_adx,
    compute_atr,
    compute_avg_daily_volume,
    compute_bb_position,
    compute_higher_lows,
    compute_macd_histogram_value,
    compute_volume_surge,
)
from services.swing.risk import build_risk_plan  # noqa: E402
from services.scoring.swing import compute_swing_score  # noqa: E402
from services.universe import SWING_UNIVERSE  # noqa: E402


# ---------------------------------------------------------------------------
# Per-symbol fetch (shared market frames passed in by caller)
# ---------------------------------------------------------------------------

def _fetch_symbol(symbol: str, period: str | None = None,
                  start: str | None = None, end: str | None = None) -> pd.DataFrame | None:
    try:
        if start is not None:
            df = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)
        else:
            df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    except Exception:  # noqa: BLE001
        return None
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def _fetch_earnings(symbol: str) -> list[pd.Timestamp]:
    try:
        df = yf.Ticker(symbol).get_earnings_dates(limit=40)
        if df is None or df.empty:
            return []
        idx = pd.to_datetime(df.index).tz_localize(None)
        return sorted(idx.tolist())
    except Exception:  # noqa: BLE001
        return []


def _fetch_inst_ownership(symbol: str) -> float | None:
    try:
        info = yf.Ticker(symbol).info or {}
        val = info.get("heldPercentInstitutions")
        if val is None:
            return None
        return round(float(val) * 100, 1)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Per-trade record (flat, CSV-friendly)
# ---------------------------------------------------------------------------

@dataclass
class ScoredTrade:
    symbol: str
    entry_date: str
    exit_date: str
    setup: str
    setup_score: float
    entry: float
    stop: float
    target: float
    rr_planned: float
    # Outcome
    exit_price: float
    exit_reason: str
    r_realized: float
    days_held: int
    # Regime context
    regime_label: str
    rr_gate: float
    regime_mult: float
    days_to_earnings: int | None
    # Scoring inputs (kept for diagnostics)
    adx_value: float | None
    ad_line_slope_pct: float | None
    higher_lows: int | None
    institutional_ownership_pct: float | None
    extended: bool
    # Score contributions (v3.0: rr, setup, macd, bb, vol)
    rr_pts: float
    setup_pts: float
    macd_hist: float | None   # raw input value
    bb_pos: float | None      # raw input value
    vol_surge_20: float | None  # raw ratio
    raw_score: float
    # Multipliers
    earnings_mult: float
    # Final
    final_score: float
    confidence: str


# ---------------------------------------------------------------------------
# Per-symbol backtest
# ---------------------------------------------------------------------------

def backtest_symbol(
    symbol: str,
    sym_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    iwm_df: pd.DataFrame,
    earnings_dates: list[pd.Timestamp],
    inst_own: float | None,
    *,
    cooloff_days: int = 5,
    use_regime: bool = True,
    use_earnings: bool = True,
    no_gates: bool = False,
) -> list[ScoredTrade]:
    trades: list[ScoredTrade] = []
    if len(sym_df) < MIN_BARS + 5:
        return trades

    next_eligible_idx = MIN_BARS

    for i in range(MIN_BARS, len(sym_df) - 1):
        if i < next_eligible_idx:
            continue
        slice_df = sym_df.iloc[: i + 1]
        bar_date = slice_df.index[-1]
        slice_spy = spy_df.loc[:bar_date]
        if len(slice_spy) < 30:
            continue
        slice_vix = vix_df.loc[:bar_date]
        slice_iwm = iwm_df.loc[:bar_date]

        # Liquidity / price gates
        price = float(slice_df["Close"].iloc[-1])
        if not no_gates:
            if price < 5.0:
                continue
            adv = compute_avg_daily_volume(slice_df, 20)
            if (adv != adv) or adv * price < 5_000_000:
                continue
        else:
            if price <= 0:
                continue

        features = _features(slice_df, slice_spy)
        if features is None:
            continue
        atr14 = compute_atr(slice_df, 14)

        cls = classify_setup(features)
        if not no_gates and cls["best_score"] < MIN_SETUP_SCORE:
            continue

        regime = (
            _regime_at(slice_spy, slice_vix, slice_iwm)
            if use_regime
            else HistRegime(label="risk_on", rr_gate=DEFAULT_RR_GATE, multiplier=1.0, disable_setups=())
        )

        if not no_gates and cls["best_setup"] in regime.disable_setups:
            continue

        dte = _days_to_next_earnings(bar_date, earnings_dates) if use_earnings else None
        if not no_gates:
            if dte is not None and dte <= 1:
                continue
            if cls["best_setup"] == "reversion" and dte is not None and dte <= 7:
                continue

        plan = build_risk_plan(
            setup=cls["best_setup"],
            current_price=price,
            atr14=atr14,
            recent_swing_low=_recent_swing_low(slice_df),
            features=features,
        )
        if no_gates:
            # Skip only truly degenerate plans (zero/negative risk)
            if plan.entry <= 0 or plan.risk_per_share <= 0:
                continue
        elif not plan.passes_gate or plan.rr < regime.rr_gate:
            continue

        # v3.0 scoring inputs
        adx_dict = compute_adx(slice_df, 14)
        adx_v = adx_dict.get("adx", float("nan")) if isinstance(adx_dict, dict) else float(adx_dict)
        ad_slope = compute_ad_line_slope(slice_df)
        hl = compute_higher_lows(slice_df)
        macd_hist_val = compute_macd_histogram_value(slice_df)
        bb_pos_val = compute_bb_position(slice_df)
        vol_surge_result = compute_volume_surge(slice_df, lookback=20)
        vol_surge_ratio = vol_surge_result.get("ratio") if isinstance(vol_surge_result, dict) else None
        extended = bool(getattr(plan, "extended", False))

        score = compute_swing_score(
            rr=plan.rr,
            setup_score=cls["best_score"],
            macd_hist_val=macd_hist_val,
            bb_position=bb_pos_val,
            vol_surge_ratio=vol_surge_ratio,
            days_to_earnings=dte,
            extended=extended,
        )

        # Hold trim across earnings
        hold_max = plan.hold_max_days
        if use_earnings and dte is not None and dte < hold_max:
            hold_max = max(1, dte - 1)

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
            ScoredTrade(
                symbol=symbol,
                entry_date=str(bar_date.date()),
                exit_date=(
                    str(fwd.index[days - 1].date())
                    if days > 0 and len(fwd) >= days
                    else str(bar_date.date())
                ),
                setup=cls["best_setup"],
                setup_score=round(cls["best_score"], 2),
                entry=round(plan.entry, 2),
                stop=round(plan.stop, 2),
                target=round(plan.target, 2),
                rr_planned=round(plan.rr, 2),
                exit_price=round(exit_price, 2),
                exit_reason=reason,
                r_realized=round(r_real, 3),
                days_held=days,
                regime_label=regime.label,
                rr_gate=regime.rr_gate,
                regime_mult=regime.multiplier,
                days_to_earnings=dte,
                adx_value=round(adx_v, 2) if adx_v == adx_v else None,
                ad_line_slope_pct=round(ad_slope, 3) if ad_slope == ad_slope else None,
                higher_lows=int(hl) if hl is not None else None,
                institutional_ownership_pct=inst_own,
                extended=extended,
                rr_pts=score["breakdown"]["rr"],
                setup_pts=score["breakdown"]["setup"],
                macd_hist=macd_hist_val,
                bb_pos=bb_pos_val,
                vol_surge_20=vol_surge_ratio,
                raw_score=score["raw_score"],
                earnings_mult=score["multipliers"]["earnings"],
                final_score=score["score"],
                confidence=score["confidence"],
            )
        )
        next_eligible_idx = i + max(days, 1) + cooloff_days

    return trades


# ---------------------------------------------------------------------------
# Universe driver
# ---------------------------------------------------------------------------

def _worker(args: tuple) -> tuple[str, list[ScoredTrade], str | None]:
    (symbol, period, start, end, spy_df, vix_df, iwm_df, cooloff, use_regime, use_earnings, no_gates) = args
    try:
        sym_df = _fetch_symbol(symbol, period=period, start=start, end=end)
        if sym_df is None or len(sym_df) < MIN_BARS + 5:
            return symbol, [], "no-data"
        earnings = _fetch_earnings(symbol) if use_earnings else []
        inst_own = _fetch_inst_ownership(symbol)
        trades = backtest_symbol(
            symbol,
            sym_df,
            spy_df,
            vix_df,
            iwm_df,
            earnings,
            inst_own,
            cooloff_days=cooloff,
            use_regime=use_regime,
            use_earnings=use_earnings,
            no_gates=no_gates,
        )
        return symbol, trades, None
    except Exception as exc:  # noqa: BLE001
        return symbol, [], f"error:{type(exc).__name__}:{exc}"


def run_universe(
    universe: list[str],
    years: int,
    workers: int,
    cooloff_days: int,
    use_regime: bool,
    use_earnings: bool,
    start: str | None = None,
    end: str | None = None,
    no_gates: bool = False,
) -> list[ScoredTrade]:
    if start is not None:
        # Add 1-year lookback buffer for indicator warmup (MA200, ADX, etc.)
        fetch_start = str((pd.Timestamp(start) - pd.DateOffset(years=1)).date())
        fetch_end   = end  # None = today
        period_or_kw = dict(start=fetch_start, end=fetch_end)
        label = f"{start} to {end or 'now'}"
    else:
        period = f"{max(years, 1) + 1}y"
        period_or_kw = dict(period=period)
        label = f"period={period}"
    print(f"Fetching market frames (SPY / ^VIX / IWM) {label} ...")
    spy_df = _fetch_symbol("SPY", **period_or_kw)
    vix_df = _fetch_symbol("^VIX", **period_or_kw)
    iwm_df = _fetch_symbol("IWM", **period_or_kw)
    if spy_df is None or vix_df is None or iwm_df is None:
        raise SystemExit("Failed to fetch SPY/VIX/IWM — aborting.")

    # Period string passed through to symbol workers (may be None for date-range mode)
    worker_period = period_or_kw.get("period")  # None when start/end used
    worker_start  = period_or_kw.get("start")
    worker_end    = period_or_kw.get("end")

    all_trades: list[ScoredTrade] = []
    failures: list[tuple[str, str]] = []
    t0 = time.time()

    payload = [
        (s, worker_period, worker_start, worker_end, spy_df, vix_df, iwm_df, cooloff_days, use_regime, use_earnings, no_gates)
        for s in universe
    ]

    print(f"Running backtest across {len(universe)} symbols with {workers} workers ...")
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_worker, p): p[0] for p in payload}
        for fut in as_completed(futs):
            sym, trades, err = fut.result()
            completed += 1
            if err:
                failures.append((sym, err))
            all_trades.extend(trades)
            if completed % 10 == 0 or completed == len(universe):
                elapsed = time.time() - t0
                print(
                    f"  [{completed:>3}/{len(universe)}]  trades={len(all_trades):<5}  "
                    f"elapsed={elapsed:6.1f}s",
                    flush=True,
                )

    if failures:
        print(f"\n{len(failures)} symbols had issues:")
        for sym, err in failures[:20]:
            print(f"  {sym:<8} {err}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")
    return all_trades


# ---------------------------------------------------------------------------
# IC / Spearman reporting
# ---------------------------------------------------------------------------

def _safe_spearman(x: pd.Series, y: pd.Series) -> tuple[float, float, int]:
    """Spearman rho, p-value, n — robust to NaN and constant series."""
    from scipy.stats import spearmanr  # local import to keep top clean

    s = pd.concat([x, y], axis=1).dropna()
    if len(s) < 10 or s.iloc[:, 0].nunique() < 2:
        return float("nan"), float("nan"), len(s)
    rho, p = spearmanr(s.iloc[:, 0], s.iloc[:, 1])
    return float(rho), float(p), len(s)


SCORE_COMPONENTS: list[str] = [
    "rr_pts",
    "setup_pts",
    "raw_score",
    "final_score",
    "rr_planned",
    "setup_score",
    "macd_hist",
    "bb_pos",
    "vol_surge_20",
    "adx_value",
    "ad_line_slope_pct",
    "higher_lows",
    "regime_mult",
    "earnings_mult",
]


def report(df: pd.DataFrame) -> None:
    if df.empty:
        print("\nNo trades produced — nothing to report.")
        return

    print("\n" + "=" * 78)
    print(f"UNIVERSE BACKTEST  —  trades={len(df)}  symbols={df['symbol'].nunique()}")
    print(f"date range: {df['entry_date'].min()}  →  {df['entry_date'].max()}")
    print("=" * 78)

    # ---- Overall outcome distribution ----
    wins = (df["exit_reason"] == "target").sum()
    losses = (df["exit_reason"] == "stop").sum()
    times = (df["exit_reason"] == "time").sum()
    print(f"\nExits: target={wins}  stop={losses}  time={times}")
    print(
        f"r_realized:  mean={df['r_realized'].mean():+.3f}  "
        f"median={df['r_realized'].median():+.3f}  "
        f"std={df['r_realized'].std():.3f}"
    )
    print(f"win rate (target only): {wins / len(df) * 100:.1f}%")
    print(f"expectancy per trade : {df['r_realized'].mean():+.3f} R")
    print(f"sum R                : {df['r_realized'].sum():+.1f}")

    # ---- Per-setup breakdown ----
    print("\n--- BY SETUP ---")
    g = df.groupby("setup").agg(
        n=("r_realized", "size"),
        mean_r=("r_realized", "mean"),
        median_r=("r_realized", "median"),
        win_rate=("exit_reason", lambda s: (s == "target").mean()),
    ).round(3)
    print(g.to_string())

    # ---- Per-component Spearman vs realised R ----
    print("\n--- SPEARMAN RHO vs r_realized (overall) ---")
    print(f"{'component':<22} {'rho':>8} {'p_value':>10} {'n':>6}")
    rows = []
    for col in SCORE_COMPONENTS:
        if col not in df.columns:
            continue
        rho, p, n = _safe_spearman(df[col], df["r_realized"])
        rows.append((col, rho, p, n))
        print(f"{col:<22} {rho:>+8.3f} {p:>10.4f} {n:>6}")

    # ---- Component IC by setup (small-sample noise expected) ----
    print("\n--- SPEARMAN RHO by setup ---")
    for setup, sub in df.groupby("setup"):
        if len(sub) < 30:
            continue
        print(f"\n  [{setup}]  n={len(sub)}")
        for col in ("rr_pts", "setup_pts", "raw_score", "final_score"):
            if col not in sub.columns:
                continue
            rho, p, n = _safe_spearman(sub[col], sub["r_realized"])
            print(f"    {col:<14} rho={rho:+.3f}  p={p:.4f}  n={n}")

    # ---- final_score decile analysis ----
    print("\n--- DECILES OF final_score vs r_realized ---")
    try:
        df = df.copy()
        df["decile"] = pd.qcut(df["final_score"], 10, labels=False, duplicates="drop") + 1
        dec = df.groupby("decile").agg(
            n=("r_realized", "size"),
            score_lo=("final_score", "min"),
            score_hi=("final_score", "max"),
            mean_r=("r_realized", "mean"),
            median_r=("r_realized", "median"),
            win_rate=("exit_reason", lambda s: (s == "target").mean()),
        ).round(3)
        print(dec.to_string())

        top = dec.iloc[-1]["mean_r"]
        bot = dec.iloc[0]["mean_r"]
        print(f"\n  top-decile mean R = {top:+.3f}   bottom-decile = {bot:+.3f}   spread = {top - bot:+.3f}")
    except ValueError as exc:
        print(f"  decile binning failed: {exc}")

    # ---- Quintile (more stable with smaller n) ----
    print("\n--- QUINTILES OF final_score vs r_realized ---")
    try:
        df["quintile"] = pd.qcut(df["final_score"], 5, labels=False, duplicates="drop") + 1
        q = df.groupby("quintile").agg(
            n=("r_realized", "size"),
            score_lo=("final_score", "min"),
            score_hi=("final_score", "max"),
            mean_r=("r_realized", "mean"),
            win_rate=("exit_reason", lambda s: (s == "target").mean()),
        ).round(3)
        print(q.to_string())
    except ValueError:
        pass

    # ---- Confidence tier analysis (matches what the UI shows) ----
    print("\n--- BY CONFIDENCE TIER ---")
    g = df.groupby("confidence").agg(
        n=("r_realized", "size"),
        mean_r=("r_realized", "mean"),
        median_r=("r_realized", "median"),
        win_rate=("exit_reason", lambda s: (s == "target").mean()),
    ).round(3)
    print(g.to_string())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--years", type=int, default=3, help="History window (default: 3)")
    p.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (overrides --years)")
    p.add_argument("--end",   type=str, default=None, help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--workers", type=int, default=8, help="Parallel symbol workers (default: 8)")
    p.add_argument("--cooloff", type=int, default=5, help="Bars between trades per symbol (default: 5)")
    p.add_argument("--no-regime", dest="regime", action="store_false")
    p.add_argument("--no-earnings", dest="earnings", action="store_false")
    p.add_argument("--no-gates", dest="no_gates", action="store_true",
                   help="Remove all quality/liquidity/RR/earnings gates (for stress-testing in adverse markets)")
    p.set_defaults(regime=True, earnings=True, no_gates=False)
    p.add_argument("--limit", type=int, default=None, help="Subset universe to first N for smoke test")
    p.add_argument("--csv", type=str, default="swing_backtest_universe.csv", help="Output ledger CSV")
    p.add_argument("--append", action="store_true", help="Append to existing CSV instead of overwriting")
    args = p.parse_args()

    universe = SWING_UNIVERSE if args.limit is None else SWING_UNIVERSE[: args.limit]

    print(f"Swing universe backtest   symbols={len(universe)}   "
          f"regime={'on' if args.regime else 'off'}   earnings={'on' if args.earnings else 'off'}"
          f"{'   GATES=OFF' if args.no_gates else ''}")

    trades = run_universe(
        universe=universe,
        years=args.years,
        workers=args.workers,
        cooloff_days=args.cooloff,
        use_regime=args.regime,
        use_earnings=args.earnings,
        start=args.start,
        end=args.end,
        no_gates=args.no_gates,
    )

    df = pd.DataFrame([asdict(t) for t in trades])

    # When --start/--end used, filter trades to the requested window only
    # (warmup bars before --start are used for indicator computation, not included in output)
    if args.start and not df.empty:
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        before = len(df)
        df = df[df["entry_date"] >= pd.Timestamp(args.start)]
        if args.end:
            df = df[df["entry_date"] <= pd.Timestamp(args.end)]
        print(f"Date filter {args.start} → {args.end or 'now'}: kept {len(df)}/{before} trades")

    if not df.empty:
        if args.append and Path(args.csv).exists():
            existing = pd.read_csv(args.csv)
            df = pd.concat([existing, df], ignore_index=True)
            df.to_csv(args.csv, index=False)
            print(f"\nAppended → {args.csv}  (total {len(df)} rows)")
        else:
            df.to_csv(args.csv, index=False)
            print(f"\nLedger written → {args.csv}  ({len(df)} rows)")
    report(df)


if __name__ == "__main__":
    main()
