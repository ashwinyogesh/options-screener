r"""
CC factor correlation analysis -- mirror of `csp_factor_correlation.py` for
the covered-call screener. Validates / falsifies audit finding HIGH-4
(Tr + SMA + SLP trend triple-count) against the CC ledger.

Usage:
    .\venv\Scripts\python.exe ..\scripts\cc_factor_correlation.py cc_backtest_full.csv
    .\venv\Scripts\python.exe ..\scripts\cc_factor_correlation.py cc_backtest_full.csv --out corr.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Force UTF-8 stdout so Unicode in summaries doesn't crash on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import pandas as pd

FACTOR_COLS = [
    "env_IVP", "env_Tr", "env_SMA", "env_SLP", "env_RSI", "env_OI",
    "strike_Delta", "strike_ROC",
]
TREND_CLUSTER = ["env_Tr", "env_SMA", "env_SLP"]
AUDIT_THRESHOLD = 0.6


def _heatmap(corr: pd.DataFrame) -> str:
    """Tiny text heatmap. Cells -> {., -, =, #, @} by abs(corr) magnitude."""
    def cell(v: float) -> str:
        a = abs(v)
        ch = "@" if a >= 0.8 else "#" if a >= 0.6 else "=" if a >= 0.4 else "-" if a >= 0.2 else "."
        sign = "+" if v >= 0 else "-"
        return f" {sign}{ch} "
    cols = list(corr.columns)
    header = "          " + "".join(f"{c:>9}" for c in cols)
    lines = [header]
    for r in cols:
        row = f"{r:>10}" + "".join(cell(corr.loc[r, c]) for c in cols)
        lines.append(row)
    legend = "\nLegend: cells are [sign][magnitude]: . = |r|<.2  - = .2-.4  = = .4-.6  # = .6-.8  @ = >=.8"
    return "\n".join(lines) + legend


def analyze(ledger_path: Path, out_path: Path | None) -> None:
    df = pd.read_csv(ledger_path)
    missing = [c for c in FACTOR_COLS if c not in df.columns]
    if missing:
        sys.exit(
            f"Ledger {ledger_path} is missing factor columns {missing}. "
            "Re-run backtest_cc.py with the latest version."
        )
    print(f"\nLoaded {len(df):,} trades from {ledger_path}")
    print(f"  Date range: {df['scan_date'].min()} -> {df['scan_date'].max()}")
    print(f"  Tickers:    {df['ticker'].nunique()}")
    print()

    factors = df[FACTOR_COLS].copy()
    corr = factors.corr(method="pearson")

    print("=" * 78)
    print("PEARSON CORRELATION MATRIX -- per-factor sub-scores (CC)")
    print("=" * 78)
    print(corr.round(2).to_string())
    print()
    print(_heatmap(corr))
    print()

    # Trend-cluster verdict
    print("=" * 78)
    print("AUDIT HIGH-4 TEST -- 'Tr + SMA + SLP triple-count trend' (CC)")
    print("=" * 78)
    pairs = [
        ("env_Tr",  "env_SMA"),
        ("env_Tr",  "env_SLP"),
        ("env_SMA", "env_SLP"),
    ]
    breaches = 0
    for a, b in pairs:
        r = corr.loc[a, b]
        flag = "  <-- BREACHES AUDIT THRESHOLD" if abs(r) >= AUDIT_THRESHOLD else ""
        if abs(r) >= AUDIT_THRESHOLD:
            breaches += 1
        print(f"  corr({a:<8}, {b:<8}) = {r:+.3f}{flag}")
    print()
    if breaches == 0:
        print(f"  VERDICT: AUDIT WRONG on this sample -- no trend-cluster pair exceeds |r| >= {AUDIT_THRESHOLD}.")
        print("           The three trend factors carry materially independent variance.")
    elif breaches == 3:
        print(f"  VERDICT: AUDIT CONFIRMED -- all three trend-cluster pairs exceed |r| >= {AUDIT_THRESHOLD}.")
        print("           Collapse {Tr, SMA, SLP} into a single 25-pt trend bundle per audit remediation.")
    else:
        print(f"  VERDICT: PARTIAL -- {breaches}/3 trend-cluster pairs exceed |r| >= {AUDIT_THRESHOLD}.")
        print("           The trend cluster is partially redundant; review case-by-case.")

    print()
    print("Trend cluster vs RSI(14) -- audit predicted co-movement in sustained trends:")
    for t in TREND_CLUSTER:
        r = corr.loc[t, "env_RSI"]
        print(f"  corr({t:<8}, env_RSI ) = {r:+.3f}")

    print()
    print("IV-percentile independence vs trend / momentum factors:")
    for t in TREND_CLUSTER + ["env_RSI"]:
        r = corr.loc["env_IVP", t]
        print(f"  corr(env_IVP , {t:<8}) = {r:+.3f}")

    print()
    print("Strike-side factors (Delta + ROC):")
    r = corr.loc["strike_Delta", "strike_ROC"]
    print(f"  corr(strike_Delta, strike_ROC) = {r:+.3f}")
    if abs(r) >= AUDIT_THRESHOLD:
        print("  NOTE: Delta and ROC are highly correlated by construction (low delta -> low premium -> low ROC).")
        print("        This is expected; the strike score is intentionally two coupled views of strike richness.")

    if out_path:
        corr.to_csv(out_path)
        print(f"\nWrote correlation matrix to {out_path}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("ledger", type=Path, help="CSV emitted by backtest_cc.py --out")
    ap.add_argument("--out", type=Path, default=None, help="Optional path to write the correlation matrix as CSV")
    args = ap.parse_args()

    if not args.ledger.exists():
        sys.exit(f"Ledger file not found: {args.ledger}")
    analyze(args.ledger, args.out)


if __name__ == "__main__":
    main()
