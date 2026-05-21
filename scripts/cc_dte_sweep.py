"""Compare CC backtest at multiple DTE windows: 14, 21, 28, 35.

Tests hypothesis: shorter DTE -> premium/stock-noise ratio improves -> better
score predictability and better alignment with CC philosophy
(generate premium, retain stock, preserve upside).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

FILES = [
    ("DTE-14", "cc_bt_dte14.csv"),
    ("DTE-21", "cc_bt_dte21.csv"),
    ("DTE-28", "cc_bt_dte28.csv"),
    ("DTE-35 (current)", "cc_backtest_full_v2.csv"),
]
LAMBDA = 1.0


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).dropna(subset=["final_score", "realised_roc_annualised", "delta", "iv_pct"])
    df["stock_only_roc"] = (df["spot_at_exp"] / df["spot"] - 1.0) * (365.0 / df["dte"]) * 100.0
    df["opp_cost"] = (df["stock_only_roc"] - df["realised_roc_annualised"]).clip(lower=0)
    df["retained"] = (df["assigned"] == 0).astype(int)
    df["phi_l1"] = df["realised_roc_annualised"] - LAMBDA * df["opp_cost"]
    df["credit_yield_pct"] = df["premium"] / df["spot"] * 100.0
    df["iv_minus_hv"] = df["iv_pct"] / 100.0 - df["hv30"]
    return df


def summarize(name: str, df: pd.DataFrame) -> dict:
    n = len(df)
    # global stats
    rho_roc, _ = spearmanr(df["final_score"], df["realised_roc_annualised"])
    rho_phi, _ = spearmanr(df["final_score"], df["phi_l1"])

    # bucket stats
    bins = [-0.1, 50, 65, 75, 85, 100.1]
    df = df.copy()
    df["bucket"] = pd.cut(df["final_score"], bins, labels=["0-50", "50-65", "65-75", "75-85", "85-100"])
    g = (
        df.groupby("bucket", observed=True)
        .agg(
            n=("final_score", "size"),
            mean_ROC=("realised_roc_annualised", "mean"),
            retain=("retained", lambda x: 100 * x.mean()),
            phi=("phi_l1", "mean"),
            mean_yield=("credit_yield_pct", "mean"),
        )
        .round(2)
    )
    means = g["mean_ROC"].values
    rets = g["retain"].values
    phis = g["phi"].values
    mono_roc = all(means[i + 1] >= means[i] for i in range(len(means) - 1))
    mono_phi = all(phis[i + 1] >= phis[i] for i in range(len(phis) - 1))
    mono_ret = all(rets[i + 1] >= rets[i] for i in range(len(rets) - 1))

    # noise / signal ratio per trade
    roc_std = df["realised_roc_annualised"].std()
    yield_mean = df["credit_yield_pct"].mean()

    # top decile
    top = df[df["final_score"] >= df["final_score"].quantile(0.90)]
    bot = df[df["final_score"] <= df["final_score"].quantile(0.10)]

    # information density: average % of P&L explained by premium
    df["abs_stock_move"] = (df["spot_at_exp"] - df["spot"]).abs()
    df["abs_pnl_share"] = df["premium"] / (df["abs_stock_move"] + df["premium"]).replace(0, np.nan)
    prem_share = df["abs_pnl_share"].mean()

    return {
        "name": name,
        "n": n,
        "rho_ROC": rho_roc,
        "rho_phi": rho_phi,
        "mono_ROC": mono_roc,
        "mono_phi": mono_phi,
        "mono_retain": mono_ret,
        "roc_std": roc_std,
        "mean_yield_pct": yield_mean,
        "premium_share_of_pnl": prem_share * 100.0,
        "top10_ROC": top["realised_roc_annualised"].mean(),
        "top10_retain": 100 * top["retained"].mean(),
        "top10_phi": top["phi_l1"].mean(),
        "spread_ROC": top["realised_roc_annualised"].mean() - bot["realised_roc_annualised"].mean(),
        "spread_phi": top["phi_l1"].mean() - bot["phi_l1"].mean(),
        "buckets": g,
    }


def main() -> None:
    summaries = []
    for name, path in FILES:
        df = load(path)
        s = summarize(name, df)
        summaries.append(s)

    print("=" * 110)
    print("  CC BACKTEST DTE SWEEP — production v3.3 scoring applied at multiple DTE windows")
    print("=" * 110)
    print()
    print(f"{'Window':<20} {'n':>6} {'rho_ROC':>8} {'rho_phi':>8} {'mono_ROC':>9} {'mono_phi':>9} {'mono_ret':>9} {'roc_std':>9} {'prem/PnL%':>10}")
    print("-" * 110)
    for r in summaries:
        mr = "PASS" if r["mono_ROC"] else "FAIL"
        mp = "PASS" if r["mono_phi"] else "FAIL"
        mret = "PASS" if r["mono_retain"] else "FAIL"
        print(f"{r['name']:<20} {r['n']:>6} {r['rho_ROC']:>+8.3f} {r['rho_phi']:>+8.3f} {mr:>9} {mp:>9} {mret:>9} {r['roc_std']:>9.1f} {r['premium_share_of_pnl']:>10.1f}")

    print()
    print(f"{'Window':<20} {'mean_yield%':>12} {'top10_ROC':>10} {'top10_retain%':>14} {'top10_phi':>10} {'spread_ROC':>11} {'spread_phi':>11}")
    print("-" * 100)
    for r in summaries:
        print(f"{r['name']:<20} {r['mean_yield_pct']:>12.2f} {r['top10_ROC']:>+10.1f} {r['top10_retain']:>14.1f} {r['top10_phi']:>+10.1f} {r['spread_ROC']:>+11.1f} {r['spread_phi']:>+11.1f}")

    print()
    print("=" * 110)
    print("  BUCKET DETAIL")
    print("=" * 110)
    for r in summaries:
        print()
        print(f"--- {r['name']}  (n={r['n']}) ---")
        print(r["buckets"].to_string())


if __name__ == "__main__":
    main()
