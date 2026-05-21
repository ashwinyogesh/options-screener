"""Quant-style CC scoring backtest.

Compares four models on cc_backtest_full.csv:
  - v3.3 baseline (current production)
  - Method D (recent hand-tuned proposal)
  - IC-z composite (cross-sectional z-score factors, IR-weight via walk-forward)
  - Ridge regression (multivariate, handles collinearity, walk-forward)

Honest evaluation:
  - All weights fit on rolling 90-day TRAINING window ending 30 days before scan_date
  - Scores produced ONLY for scan_dates outside that window
  - No look-ahead

Outcome target: phi_l1 = realised_roc_annualised - 1.0 * opp_cost
                (per session-confirmed CC philosophy, lambda = 1.0)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

CSV = "cc_backtest_full_v2.csv"
LAMBDA = 1.0
TRAIN_DAYS = 90
EMBARGO_DAYS = 30  # gap between train window end and scoring date (avoid leakage via overlapping option expiries)
IC_HALFLIFE = 30


# ----------------------------- load & enrich ----------------------------------
def load() -> pd.DataFrame:
    df = pd.read_csv(CSV, parse_dates=["scan_date", "expiry_date"]).dropna(
        subset=["final_score", "realised_roc_annualised", "delta", "iv_pct"]
    )
    df["stock_only_roc"] = (df["spot_at_exp"] / df["spot"] - 1.0) * (365.0 / df["dte"]) * 100.0
    df["opp_cost"] = (df["stock_only_roc"] - df["realised_roc_annualised"]).clip(lower=0)
    df["retained"] = (df["assigned"] == 0).astype(int)
    df["phi_l1"] = df["realised_roc_annualised"] - LAMBDA * df["opp_cost"]
    # derived raw features
    df["moneyness"] = df["strike"] / df["spot"]
    df["credit_yield"] = df["premium"] / df["spot"]
    df["iv_minus_hv"] = df["iv_pct"] / 100.0 - df["hv30"]  # iv_pct is percentile 0-100, hv30 is decimal; OK as proxy
    df["log_dte"] = np.log(df["dte"].clip(lower=1))
    return df.sort_values("scan_date").reset_index(drop=True)


RAW_FEATURES = [
    "iv_pct",         # higher = juicier premium
    "delta",          # OTM-ness
    "dist52w",        # 52w distance
    "rsi",            # momentum
    "sma_ratio",      # spot/sma50
    "sma50_slope_pct",
    "hv30",
    "moneyness",
    "credit_yield",
    "iv_minus_hv",
    "log_dte",
]


# --------------------- cross-sectional z-score per scan_date -------------------
def cs_zscore(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    z = df.copy()
    for c in cols:
        grp = z.groupby("scan_date")[c]
        mu = grp.transform("mean")
        sd = grp.transform("std").replace(0, np.nan)
        z[c + "_z"] = ((z[c] - mu) / sd).fillna(0.0).clip(-3, 3)
    return z


# --------------------- walk-forward IC weights ---------------------------------
def walk_forward_ic(df: pd.DataFrame, feature_cols: list[str]) -> pd.Series:
    """Return per-row composite score (sum of z * IC_weight) using only past data."""
    dates = sorted(df["scan_date"].unique())
    score = pd.Series(np.nan, index=df.index)
    feat_z = [f + "_z" for f in feature_cols]

    for d in dates:
        train_end = d - pd.Timedelta(days=EMBARGO_DAYS)
        train_start = train_end - pd.Timedelta(days=TRAIN_DAYS)
        train = df[(df["scan_date"] >= train_start) & (df["scan_date"] <= train_end)]
        if len(train) < 200:
            continue  # not enough history
        # per-factor IC on training window (Spearman of factor vs phi_l1)
        ics = {}
        for f in feat_z:
            s = train.dropna(subset=[f, "phi_l1"])
            if len(s) < 100:
                ics[f] = 0.0
                continue
            rho, _ = spearmanr(s[f], s["phi_l1"])
            ics[f] = 0.0 if np.isnan(rho) else rho
        # weight = IC (sign preserved). Normalize so |w| sums to 1.
        ic_arr = np.array([ics[f] for f in feat_z])
        if np.abs(ic_arr).sum() < 1e-6:
            continue
        w = ic_arr / np.abs(ic_arr).sum()
        # score current scan_date rows
        mask = df["scan_date"] == d
        score.loc[mask] = (df.loc[mask, feat_z].values * w).sum(axis=1)
    return score


# --------------------- walk-forward Ridge regression --------------------------
def walk_forward_ridge(df: pd.DataFrame, feature_cols: list[str], alpha: float = 5.0) -> pd.Series:
    dates = sorted(df["scan_date"].unique())
    score = pd.Series(np.nan, index=df.index)
    feat_z = [f + "_z" for f in feature_cols]

    for d in dates:
        train_end = d - pd.Timedelta(days=EMBARGO_DAYS)
        train_start = train_end - pd.Timedelta(days=TRAIN_DAYS)
        train = df[(df["scan_date"] >= train_start) & (df["scan_date"] <= train_end)].dropna(
            subset=feat_z + ["phi_l1"]
        )
        if len(train) < 200:
            continue
        X = train[feat_z].values
        y = train["phi_l1"].values
        model = Ridge(alpha=alpha)
        model.fit(X, y)
        mask = df["scan_date"] == d
        if not mask.any():
            continue
        score.loc[mask] = model.predict(df.loc[mask, feat_z].values)
    return score


# --------------------- Method D simulated score (for comparison) ---------------
def method_d_score(df: pd.DataFrame) -> pd.Series:
    env_IVP = df["env_IVP"] * (80.0 / 35.0)
    env_OI = df["env_OI"]
    env = (env_IVP + env_OI).clip(0, 100)

    def tent(d, ideal=0.30, half=0.15, max_pts=25):
        return np.maximum(0.0, max_pts * (1.0 - np.abs(d - ideal) / half))

    sD = tent(df["delta"])
    sROC = df["strike_ROC"] * (45.0 / 35.0)
    strike = (sD + sROC) * 100.0 / 70.0  # BA+LQ absent in backtest -> rescale
    return 0.4 * env + 0.6 * strike


# --------------------- to 0-100 via cross-sectional percentile -----------------
def to_pct100(score: pd.Series, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=score.index)
    for d, idx in df.groupby("scan_date").groups.items():
        s = score.loc[idx]
        if s.notna().sum() < 5:
            continue
        out.loc[idx] = s.rank(pct=True) * 100.0
    return out


# --------------------- evaluation ----------------------------------------------
def evaluate(name: str, score: pd.Series, df: pd.DataFrame) -> dict:
    s = df.assign(score=score).dropna(subset=["score"])
    if len(s) < 100:
        return {"model": name, "n": len(s), "note": "insufficient data"}
    rho_roc, p_roc = spearmanr(s["score"], s["realised_roc_annualised"])
    rho_phi, p_phi = spearmanr(s["score"], s["phi_l1"])
    # bucket performance
    bins = [-0.1, 50, 65, 75, 85, 100.1]
    s = s.copy()
    s["bucket"] = pd.cut(s["score"], bins, labels=["0-50", "50-65", "65-75", "75-85", "85-100"])
    g = (
        s.groupby("bucket", observed=True)
        .agg(
            n=("score", "size"),
            mean_ROC=("realised_roc_annualised", "mean"),
            retain=("retained", lambda x: 100 * x.mean()),
            phi=("phi_l1", "mean"),
        )
        .round(2)
    )
    means = g["mean_ROC"].values
    phis = g["phi"].values
    mono_roc = all(means[i + 1] >= means[i] for i in range(len(means) - 1))
    mono_phi = all(phis[i + 1] >= phis[i] for i in range(len(phis) - 1))
    # top decile vs bottom decile
    top = s[s["score"] >= s["score"].quantile(0.90)]
    bot = s[s["score"] <= s["score"].quantile(0.10)]
    spread_phi = top["phi_l1"].mean() - bot["phi_l1"].mean()
    spread_roc = top["realised_roc_annualised"].mean() - bot["realised_roc_annualised"].mean()
    return {
        "model": name,
        "n": len(s),
        "rho_ROC": rho_roc,
        "rho_phi": rho_phi,
        "p_phi": p_phi,
        "mono_ROC": mono_roc,
        "mono_phi": mono_phi,
        "top10_phi": top["phi_l1"].mean(),
        "bot10_phi": bot["phi_l1"].mean(),
        "spread_phi": spread_phi,
        "spread_ROC": spread_roc,
        "top10_retain": 100 * top["retained"].mean(),
        "buckets": g,
    }


# --------------------- main ----------------------------------------------------
def main() -> None:
    df = load()
    print("=" * 78)
    print(f"  QUANT CC BACKTEST   n={len(df)} trades, {df.ticker.nunique()} tickers")
    print(f"  {df.scan_date.min().date()} -> {df.scan_date.max().date()}")
    print(f"  Target: phi_l1 (lambda={LAMBDA})    Walk-forward: train={TRAIN_DAYS}d, embargo={EMBARGO_DAYS}d")
    print("=" * 78)

    df = cs_zscore(df, RAW_FEATURES)

    # === scores ===
    score_v33 = df["final_score"]
    score_md = method_d_score(df)
    score_ic_raw = walk_forward_ic(df, RAW_FEATURES)
    score_ridge_raw = walk_forward_ridge(df, RAW_FEATURES, alpha=5.0)
    # rescale quant scores to 0-100 via cross-sectional percentile so band semantics match
    score_ic = to_pct100(score_ic_raw, df)
    score_ridge = to_pct100(score_ridge_raw, df)

    # === also try an honest walk-forward Method D-style with z-scored IVP+delta+ROC only ===
    score_md_ic = walk_forward_ic(df, ["iv_pct", "delta", "credit_yield"])
    score_md_ic_pct = to_pct100(score_md_ic, df)

    # === lean strong-IC features only (top 5 by IC) ===
    STRONG = ["iv_pct", "dist52w", "iv_minus_hv", "sma50_slope_pct", "rsi"]
    score_strong = walk_forward_ic(df, STRONG)
    score_strong_pct = to_pct100(score_strong, df)

    # === walk-forward fit of Method D-shape (IVP heavy + delta-tent + ROC, but weights fit OOS) ===
    # Use ridge on a 3-feature set that mirrors Method D's idea
    score_md_wf = walk_forward_ridge(df, ["iv_pct", "delta", "credit_yield"], alpha=2.0)
    score_md_wf_pct = to_pct100(score_md_wf, df)

    results = [
        evaluate("v3.3 baseline (production)", score_v33, df),
        evaluate("Method D (in-sample, optimistic)", score_md, df),
        evaluate("Method D-shape Ridge (walk-fwd)", score_md_wf_pct, df),
        evaluate("IC-z all-11 (walk-fwd)", score_ic, df),
        evaluate("IC-z lean IVP+delta+yield (wf)", score_md_ic_pct, df),
        evaluate("IC-z STRONG-5 (walk-fwd)", score_strong_pct, df),
        evaluate("Ridge all-11 (walk-fwd)", score_ridge, df),
    ]

    # === summary table ===
    print()
    print(f"{'Model':<38} {'n':>6} {'rho_ROC':>9} {'rho_phi':>9} {'mono_ROC':>9} {'mono_phi':>9} {'top10_phi':>10} {'bot10_phi':>10} {'spread':>8} {'retain%':>8}")
    print("-" * 130)
    for r in results:
        if "note" in r:
            print(f"{r['model']:<38} {r['n']:>6}  {r['note']}")
            continue
        mr = "PASS" if r["mono_ROC"] else "FAIL"
        mp = "PASS" if r["mono_phi"] else "FAIL"
        print(f"{r['model']:<38} {r['n']:>6} {r['rho_ROC']:>+9.3f} {r['rho_phi']:>+9.3f} {mr:>9} {mp:>9} {r['top10_phi']:>+10.1f} {r['bot10_phi']:>+10.1f} {r['spread_phi']:>+8.1f} {r['top10_retain']:>8.1f}")

    # === bucket detail for top quant model ===
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

    # === diagnostic: average IC weights from final training window ===
    print()
    print("=" * 78)
    print("  AVERAGE IC WEIGHTS (final training window, IC-z composite, 11 features)")
    print("=" * 78)
    last_d = df["scan_date"].max()
    train_end = last_d - pd.Timedelta(days=EMBARGO_DAYS)
    train_start = train_end - pd.Timedelta(days=TRAIN_DAYS)
    train = df[(df["scan_date"] >= train_start) & (df["scan_date"] <= train_end)]
    feat_z = [f + "_z" for f in RAW_FEATURES]
    ics = {}
    for f in feat_z:
        s = train.dropna(subset=[f, "phi_l1"])
        if len(s) >= 100:
            rho, _ = spearmanr(s[f], s["phi_l1"])
            ics[f.replace("_z", "")] = rho
    for k, v in sorted(ics.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {k:<22}  IC = {v:+.3f}")


if __name__ == "__main__":
    main()
