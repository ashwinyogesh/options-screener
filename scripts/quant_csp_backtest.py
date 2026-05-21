"""Quant-style CSP scoring backtest — mirror of quant_cc_backtest.py.

CSP philosophy differs from CC: assignment is acceptable (we want to own
these stocks at a discount). Outcome target is just `realised_roc_annualised`
— no opportunity-cost penalty.

Compares:
  - v3.3 / production Method D (current CSP code path)
  - In-sample raw-IVP heavy (= what Method D looks like)
  - IC-z all-11 (walk-fwd)
  - IC-z lean (walk-fwd)
  - IC-z STRONG-5 (walk-fwd)
  - Ridge all-11 (walk-fwd)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

CSV = "csp_backtest_full_v2.csv"
TRAIN_DAYS = 90
EMBARGO_DAYS = 30


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV, parse_dates=["scan_date", "expiry_date"]).dropna(
        subset=["final_score", "realised_roc_annualised", "delta", "iv_pct"]
    )
    df["moneyness"] = df["strike"] / df["spot"]
    df["credit_yield"] = df["premium"] / df["strike"]  # CSP: collateral is strike
    df["iv_minus_hv"] = df["iv_pct"] / 100.0 - df["hv30"]
    df["log_dte"] = np.log(df["dte"].clip(lower=1))
    df["abs_delta"] = df["delta"].abs()  # CSP deltas are negative
    return df.sort_values("scan_date").reset_index(drop=True)


RAW_FEATURES = [
    "iv_pct",
    "abs_delta",
    "dist52w",
    "rsi",
    "sma_ratio",
    "sma50_slope_pct",
    "hv30",
    "moneyness",
    "credit_yield",
    "iv_minus_hv",
    "log_dte",
]


def cs_zscore(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    z = df.copy()
    for c in cols:
        grp = z.groupby("scan_date")[c]
        mu = grp.transform("mean")
        sd = grp.transform("std").replace(0, np.nan)
        z[c + "_z"] = ((z[c] - mu) / sd).fillna(0.0).clip(-3, 3)
    return z


def walk_forward_ic(df: pd.DataFrame, feature_cols: list[str]) -> pd.Series:
    dates = sorted(df["scan_date"].unique())
    score = pd.Series(np.nan, index=df.index)
    feat_z = [f + "_z" for f in feature_cols]
    for d in dates:
        train_end = d - pd.Timedelta(days=EMBARGO_DAYS)
        train_start = train_end - pd.Timedelta(days=TRAIN_DAYS)
        train = df[(df["scan_date"] >= train_start) & (df["scan_date"] <= train_end)]
        if len(train) < 200:
            continue
        ics = {}
        for f in feat_z:
            s = train.dropna(subset=[f, "realised_roc_annualised"])
            if len(s) < 100:
                ics[f] = 0.0
                continue
            rho, _ = spearmanr(s[f], s["realised_roc_annualised"])
            ics[f] = 0.0 if np.isnan(rho) else rho
        ic_arr = np.array([ics[f] for f in feat_z])
        if np.abs(ic_arr).sum() < 1e-6:
            continue
        w = ic_arr / np.abs(ic_arr).sum()
        mask = df["scan_date"] == d
        score.loc[mask] = (df.loc[mask, feat_z].values * w).sum(axis=1)
    return score


def walk_forward_ridge(df: pd.DataFrame, feature_cols: list[str], alpha: float = 5.0) -> pd.Series:
    dates = sorted(df["scan_date"].unique())
    score = pd.Series(np.nan, index=df.index)
    feat_z = [f + "_z" for f in feature_cols]
    for d in dates:
        train_end = d - pd.Timedelta(days=EMBARGO_DAYS)
        train_start = train_end - pd.Timedelta(days=TRAIN_DAYS)
        train = df[(df["scan_date"] >= train_start) & (df["scan_date"] <= train_end)].dropna(
            subset=feat_z + ["realised_roc_annualised"]
        )
        if len(train) < 200:
            continue
        X = train[feat_z].values
        y = train["realised_roc_annualised"].values
        model = Ridge(alpha=alpha)
        model.fit(X, y)
        mask = df["scan_date"] == d
        if not mask.any():
            continue
        score.loc[mask] = model.predict(df.loc[mask, feat_z].values)
    return score


def to_pct100(score: pd.Series, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=score.index)
    for d, idx in df.groupby("scan_date").groups.items():
        s = score.loc[idx]
        if s.notna().sum() < 5:
            continue
        out.loc[idx] = s.rank(pct=True) * 100.0
    return out


def evaluate(name: str, score: pd.Series, df: pd.DataFrame) -> dict:
    s = df.assign(score=score).dropna(subset=["score"])
    if len(s) < 100:
        return {"model": name, "n": len(s), "note": "insufficient"}
    rho, p = spearmanr(s["score"], s["realised_roc_annualised"])
    bins = [-0.1, 50, 65, 75, 85, 100.1]
    s = s.copy()
    s["bucket"] = pd.cut(s["score"], bins, labels=["0-50", "50-65", "65-75", "75-85", "85-100"])
    g = (
        s.groupby("bucket", observed=True)
        .agg(
            n=("score", "size"),
            mean_ROC=("realised_roc_annualised", "mean"),
            win=("pnl_per_contract", lambda x: 100 * (x > 0).mean()),
            assign=("assigned", lambda x: 100 * x.mean()),
        )
        .round(2)
    )
    means = g["mean_ROC"].values
    mono = all(means[i + 1] >= means[i] for i in range(len(means) - 1))
    top = s[s["score"] >= s["score"].quantile(0.90)]
    bot = s[s["score"] <= s["score"].quantile(0.10)]
    return {
        "model": name,
        "n": len(s),
        "rho": rho,
        "mono": mono,
        "top10_ROC": top["realised_roc_annualised"].mean(),
        "top10_win": 100 * (top["pnl_per_contract"] > 0).mean(),
        "top10_assign": 100 * top["assigned"].mean(),
        "spread_ROC": top["realised_roc_annualised"].mean() - bot["realised_roc_annualised"].mean(),
        "buckets": g,
    }


def main() -> None:
    df = load()
    print("=" * 78)
    print(f"  QUANT CSP BACKTEST   n={len(df)}, {df.ticker.nunique()} tickers")
    print(f"  {df.scan_date.min().date()} -> {df.scan_date.max().date()}")
    print(f"  Target: realised_roc_annualised    Walk-fwd: train={TRAIN_DAYS}d, embargo={EMBARGO_DAYS}d")
    print("=" * 78)
    df = cs_zscore(df, RAW_FEATURES)

    score_md = df["final_score"]  # production Method D
    score_ic = to_pct100(walk_forward_ic(df, RAW_FEATURES), df)
    STRONG = ["iv_pct", "dist52w", "iv_minus_hv", "sma50_slope_pct", "rsi"]
    score_strong = to_pct100(walk_forward_ic(df, STRONG), df)
    score_lean = to_pct100(walk_forward_ic(df, ["iv_pct", "abs_delta", "credit_yield"]), df)
    score_ridge = to_pct100(walk_forward_ridge(df, RAW_FEATURES), df)

    results = [
        evaluate("CSP Method D (production)", score_md, df),
        evaluate("IC-z all-11 (walk-fwd)", score_ic, df),
        evaluate("IC-z STRONG-5 (walk-fwd)", score_strong, df),
        evaluate("IC-z lean IVP+delta+yield (wf)", score_lean, df),
        evaluate("Ridge all-11 (walk-fwd)", score_ridge, df),
    ]

    print()
    print(f"{'Model':<38} {'n':>6} {'rho':>8} {'mono':>6} {'top10_ROC':>10} {'top10_win':>10} {'top10_assn':>11} {'spread':>8}")
    print("-" * 110)
    for r in results:
        if "note" in r:
            print(f"{r['model']:<38} {r['n']:>6}  {r['note']}")
            continue
        m = "PASS" if r["mono"] else "FAIL"
        print(f"{r['model']:<38} {r['n']:>6} {r['rho']:>+8.3f} {m:>6} {r['top10_ROC']:>+10.1f} {r['top10_win']:>10.1f} {r['top10_assign']:>11.1f} {r['spread_ROC']:>+8.1f}")

    print()
    print("=" * 78)
    print("  BUCKET DETAIL")
    print("=" * 78)
    for r in results:
        if "buckets" not in r:
            continue
        print()
        print(f"--- {r['model']} ---")
        print(r["buckets"].to_string())

    # IC weights
    print()
    print("=" * 78)
    print("  IC WEIGHTS (final training window, 11 features, target=realised_roc)")
    print("=" * 78)
    last_d = df["scan_date"].max()
    train_end = last_d - pd.Timedelta(days=EMBARGO_DAYS)
    train_start = train_end - pd.Timedelta(days=TRAIN_DAYS)
    train = df[(df["scan_date"] >= train_start) & (df["scan_date"] <= train_end)]
    feat_z = [f + "_z" for f in RAW_FEATURES]
    ics = {}
    for f in feat_z:
        s = train.dropna(subset=[f, "realised_roc_annualised"])
        if len(s) >= 100:
            rho, _ = spearmanr(s[f], s["realised_roc_annualised"])
            ics[f.replace("_z", "")] = rho
    for k, v in sorted(ics.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {k:<22}  IC = {v:+.3f}")


if __name__ == "__main__":
    main()
