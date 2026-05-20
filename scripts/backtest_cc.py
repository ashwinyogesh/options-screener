r"""
CC screener backtest CLI -- thin wrapper around `services.cc_backtest_service`.

The engine lives in `backend/services/cc_backtest_service.py` so the API
endpoint and this script share exactly one implementation. Mirrors
`scripts/backtest_csp.py` for the covered-call side.

================================================================================
Usage
================================================================================

    cd backend
    .\venv\Scripts\python.exe ..\scripts\backtest_cc.py --years 3 --dte 35
    .\venv\Scripts\python.exe ..\scripts\backtest_cc.py --tickers NVDA,PLTR --years 2
    .\venv\Scripts\python.exe ..\scripts\backtest_cc.py --weekly-step 2 --out cc_bt.csv

================================================================================
Outputs
================================================================================

Console: per-bucket summary table + monotonicity verdict.
CSV (--out): one row per trade with all scoring inputs and outcome columns.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Force UTF-8 stdout so Unicode in summaries doesn't crash on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np
import pandas as pd

# Make `services.*` imports work when run as `python scripts/backtest_cc.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.cc_backtest_service import (  # noqa: E402
    DEFAULT_DTE,
    DEFAULT_RF,
    DEFAULT_WEEKLY_STEP,
    SCORE_BUCKETS,
    backtest_universe,
)
from services.universe import MOMENTUM_UNIVERSE  # noqa: E402

logger = logging.getLogger("backtest_cc")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_YEARS = 3  # CLI default -- UI defaults to 2 for latency


# ---------------------------------------------------------------------------
# Summary printing (CLI-only; the API consumes the dataclass directly)
# ---------------------------------------------------------------------------

def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = returns.cumsum()
    peak = equity.cummax()
    return float((equity - peak).min())


def summarise(df: pd.DataFrame) -> None:
    if df.empty:
        print("\nNo trades generated. Try a longer --years or different --tickers.")
        return

    print(f"\n{'=' * 78}")
    print(f"CC BACKTEST RESULTS -- {len(df)} trades across {df['ticker'].nunique()} tickers")
    print(f"  Date range: {df['scan_date'].min()} -> {df['scan_date'].max()}")
    print(f"{'=' * 78}\n")

    rows = []
    for lo, hi, label in SCORE_BUCKETS:
        sub = df[(df["final_score"] >= lo) & (df["final_score"] < hi)]
        if sub.empty:
            rows.append({
                "bucket": label, "n": 0,
                "mean_ROC%": np.nan, "median_ROC%": np.nan,
                "Sharpe": np.nan, "win_rate%": np.nan, "called_rate%": np.nan,
                "max_DD$": np.nan,
            })
            continue
        roc = sub["realised_roc_annualised"]
        pnl = sub["pnl_per_contract"]
        sharpe = roc.mean() / roc.std(ddof=1) if roc.std(ddof=1) > 0 else np.nan
        # CC engine reuses the `assigned` boolean for "called away".
        called = sub["assigned"] if "assigned" in sub.columns else sub.get("called", pd.Series(dtype=float))
        rows.append({
            "bucket": label,
            "n": len(sub),
            "mean_ROC%": round(roc.mean(), 1),
            "median_ROC%": round(roc.median(), 1),
            "Sharpe": round(sharpe, 2) if not np.isnan(sharpe) else np.nan,
            "win_rate%": round((pnl > 0).mean() * 100, 1),
            "called_rate%": round(called.mean() * 100, 1) if not called.empty else np.nan,
            "max_DD$": round(_max_drawdown(pnl), 0),
        })

    bucket_df = pd.DataFrame(rows)
    print("Per-score-bucket performance:")
    print(bucket_df.to_string(index=False))

    print("\nMonotonicity test (THE audit's headline question):")
    populated = [r for r in rows if r["n"] > 0]
    means = [r["mean_ROC%"] for r in populated]
    is_monotone = all(means[i] <= means[i + 1] for i in range(len(means) - 1))
    print(f"  Bucket means monotone non-decreasing? {'YES' if is_monotone else 'NO'}")
    print(f"  Sequence: {' -> '.join(f'{m:+.1f}' for m in means)}")

    from scipy.stats import spearmanr  # type: ignore
    rho, p = spearmanr(df["final_score"], df["realised_roc_annualised"])
    print(f"  Spearman(score, realised_ROC) rho = {rho:+.3f}   p = {p:.4f}")
    verdict = "PASS" if (rho > 0 and p < 0.05) else "FAIL"
    print(f"  Verdict: {verdict} -- {'score has a monotonic relationship to realised ROC' if verdict == 'PASS' else 'no detectable signal in the scoring function on this sample'}")

    print("\n65-cutoff check (production tradeable threshold):")
    above = df[df["final_score"] >= 65]
    below = df[df["final_score"] < 65]
    if not above.empty and not below.empty:
        diff = above["realised_roc_annualised"].mean() - below["realised_roc_annualised"].mean()
        called_col = "assigned" if "assigned" in df.columns else "called"
        print(f"  >=65: n={len(above)}  mean ROC = {above['realised_roc_annualised'].mean():+.1f}%   "
              f"called rate = {above[called_col].mean() * 100:.1f}%")
        print(f"  <65:  n={len(below)}  mean ROC = {below['realised_roc_annualised'].mean():+.1f}%   "
              f"called rate = {below[called_col].mean() * 100:.1f}%")
        print(f"  Delta (above - below) = {diff:+.1f}%   "
              f"{'PASS -- threshold has signal' if diff > 0 else 'FAIL -- threshold does not separate winners from losers'}")
    else:
        print("  Not enough trades on both sides of 65 to test.")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS,
                    help=f"Years of history (default {DEFAULT_YEARS})")
    ap.add_argument("--dte", type=int, default=DEFAULT_DTE,
                    help=f"Days to expiration per trade (default {DEFAULT_DTE})")
    ap.add_argument("--weekly-step", type=int, default=DEFAULT_WEEKLY_STEP,
                    help=f"Weeks between scan dates (default {DEFAULT_WEEKLY_STEP})")
    ap.add_argument("--rf", type=float, default=DEFAULT_RF,
                    help=f"Annualised risk-free rate (default {DEFAULT_RF})")
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated tickers (default: full MOMENTUM_UNIVERSE)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Take the first N tickers from the universe (speed)")
    ap.add_argument("--out", type=str, default=None,
                    help="Write the per-trade ledger to this CSV path")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = list(MOMENTUM_UNIVERSE)
    if args.limit:
        tickers = tickers[: args.limit]

    df = backtest_universe(
        tickers,
        years=args.years,
        dte=args.dte,
        weekly_step=args.weekly_step,
        rf=args.rf,
    )

    summarise(df)

    if args.out:
        out_path = Path(args.out)
        df.to_csv(out_path, index=False)
        logger.info("Wrote %d trades to %s", len(df), out_path)


if __name__ == "__main__":
    main()
