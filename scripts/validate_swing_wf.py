#!/usr/bin/env python3
"""Walk-forward validation of swing composite scorer.

Tests whether the composite (30% v3.0 rank + 70% Lasso rank) has genuine
out-of-sample predictive power, and whether that IC holds across regimes
(risk_on / neutral / risk_off).

Usage:
    python scripts/validate_swing_wf.py

Outputs:
  1. Walk-forward OOS IC per window — overall and per regime
  2. Win rate and median R at composite >= 80 per window
  3. Risk-off deep-dive (all 24 trades, scored by composite)
  4. Summary: is the composite trustworthy?
"""

import warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

CSV = "swing_backtest_universe_enriched.csv"

# Features available in the CSV that map to live Lasso inputs.
# Excludes outcome columns, score outputs, and identifiers.
LASSO_FEATURES = [
    "rr_planned",
    "setup_score_norm",   # derived: setup_pts / 30 * 100
    "adx_value",
    "dist_sma50",
    "vix_vs_med20",
    "higher_lows",
    "institutional_ownership_pct",
    "macd_hist",
    "bb_pos",
    "vol_surge_20",
    "atr_pct",
    "base_length",
    "base_depth",
    "pct_off_52w_high",
    "ret_1m",
    "ret_3m",
    "rs_vs_spy_3m",
    "spy_slope_50",
    "spy_ret_5d",
    "vix_level",
    "dist_sma20",
    "dist_sma200",
    "log_price",
    "gap_up",
    "nr7",
    "inside_bar",
    "days_to_earnings",
    "extended",
]

# Walk-forward windows: (train_end, test_start, test_end) as integer row indices
WF_WINDOWS = [
    (2000, 2000, 2500),
    (2500, 2500, 3000),
    (3000, 3000, None),   # None = rest of dataset
]


# ---------------------------------------------------------------------------
# v3.0 additive score reconstruction from CSV raw components
# ---------------------------------------------------------------------------

def _rr_pts_v3(entry: pd.Series, stop: pd.Series, target: pd.Series) -> pd.Series:
    """v3.0 R:R scoring: rr <= 2.5 → 0, rr = 3.0 → 40 (linear, clamped 0-40)."""
    rr = (target - entry) / (entry - stop).clip(lower=0.01)
    return ((rr - 2.5) / 0.5 * 40).clip(0, 40)


def _macd_pts_v3(hist: pd.Series) -> pd.Series:
    """v3.0 MACD histogram scoring: positive histogram → up to 25 pts."""
    ref = hist[hist > 0].quantile(0.85) if (hist > 0).any() else 1.0
    pts = (hist / max(ref, 1e-6) * 25).clip(0, 25)
    pts[hist <= 0] = 0.0
    return pts


def _bb_pts_v3(bb_pos: pd.Series) -> pd.Series:
    """v3.0 BB position scoring: 0-1 range → 0-20 pts (linear)."""
    return (bb_pos.clip(0, 1) * 20).fillna(10.0)


def _vol_pts_v3(surge: pd.Series) -> pd.Series:
    """v3.0 volume surge scoring."""
    pts = pd.Series(0.0, index=surge.index)
    pts[surge >= 1.5] = 5.0
    pts[surge >= 2.0] = 10.0
    return pts


def reconstruct_v3(df: pd.DataFrame) -> pd.Series:
    """Reconstruct v3.0 additive score (0-100) from CSV raw components."""
    rr    = _rr_pts_v3(df["entry"], df["stop"], df["target"])
    setup = df["setup_pts"].clip(0, 30)
    macd  = _macd_pts_v3(df["macd_hist"])
    bb    = _bb_pts_v3(df["bb_pos"])
    vol   = _vol_pts_v3(df["vol_surge_20"])
    raw   = rr + setup + macd + bb + vol                          # max 125
    emult = df["earnings_mult"].fillna(1.0).clip(0.5, 1.0)
    return (raw.clip(0, 125) / 125 * 100 * emult).clip(0, 100)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def composite_score(v3: pd.Series, lasso_p: pd.Series) -> pd.Series:
    return (0.30 * pct_rank(v3) + 0.70 * pct_rank(lasso_p)) * 100


def ic_row(label: str, scores: pd.Series, outcomes: pd.Series) -> dict:
    n = len(scores)
    if n < 10:
        return {"window": label, "n": n, "IC_rho": float("nan"), "p_val": float("nan")}
    rho, p = spearmanr(scores, outcomes)
    return {"window": label, "n": n, "IC_rho": rho, "p_val": p}


def band_stats(scores: pd.Series, hit: pd.Series, r: pd.Series,
               threshold: int = 80) -> dict:
    mask = scores >= threshold
    n_band = mask.sum()
    if n_band == 0:
        return {"n_band": 0, "win_pct": float("nan"), "median_r": float("nan")}
    return {
        "n_band": int(n_band),
        "win_pct": round(hit[mask].mean() * 100, 1),
        "median_r": round(r[mask].median(), 2),
    }


def fit_lasso(train: pd.DataFrame, features: list[str]) -> Pipeline:
    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            penalty="l1", solver="liblinear",
            C=0.1, max_iter=1000, random_state=42,
        )),
    ])
    X = train[features]
    y = (train["exit_reason"] == "target").astype(int)
    pipe.fit(X, y)
    return pipe


def predict_lasso(pipe: Pipeline, test: pd.DataFrame,
                  features: list[str], train_medians: pd.Series) -> pd.Series:
    X = test[features]
    return pd.Series(pipe.predict_proba(X)[:, 1], index=test.index)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    df = pd.read_csv(CSV, parse_dates=["entry_date"]).sort_values("entry_date").reset_index(drop=True)

    # Derived features
    df["rr_planned"] = (df["target"] - df["entry"]) / (df["entry"] - df["stop"]).clip(lower=0.01)
    df["setup_score_norm"] = df["setup_pts"] / 30.0 * 100.0
    df["hit_target"] = (df["exit_reason"] == "target").astype(int)
    df["v3_score"] = reconstruct_v3(df)

    # Impute inst_own with global median (173 nulls)
    io_med = df["institutional_ownership_pct"].median()
    df["institutional_ownership_pct"] = df["institutional_ownership_pct"].fillna(io_med)

    available = [f for f in LASSO_FEATURES if f in df.columns]
    missing = [f for f in LASSO_FEATURES if f not in df.columns]
    if missing:
        print(f"WARNING: features not in CSV (will be skipped): {missing}")

    print("=" * 72)
    print(f"SWING COMPOSITE WALK-FORWARD VALIDATION  (n={len(df)} trades)")
    print(f"Date range: {df['entry_date'].min().date()} → {df['entry_date'].max().date()}")
    print(f"Features used for Lasso refit: {len(available)}")
    print("=" * 72)

    # -----------------------------------------------------------------------
    # Overall in-sample baseline (for comparison)
    # -----------------------------------------------------------------------
    print("\n── In-sample baseline (NOT a valid forward estimate) ──")
    pipe_all = fit_lasso(df, available)
    df["lasso_p_is"] = predict_lasso(pipe_all, df, available, None)
    df["composite_is"] = composite_score(df["v3_score"], df["lasso_p_is"])

    for col, label in [("final_score", "v2 score (old)"),
                        ("v3_score",    "v3.0 additive (reconstructed)"),
                        ("lasso_p_is",  "Lasso P(target) in-sample"),
                        ("composite_is","Composite in-sample")]:
        rho, p = spearmanr(df[col], df["r_realized"])
        print(f"  {label:40s}  IC={rho:+.3f}  p={p:.4f}")

    # -----------------------------------------------------------------------
    # Walk-forward OOS
    # -----------------------------------------------------------------------
    print("\n── Walk-forward OOS results ──")
    ic_rows   = []
    band_rows = []
    oos_parts = []

    for tr_end, te_start, te_end in WF_WINDOWS:
        train = df.iloc[:tr_end]
        test  = df.iloc[te_start:te_end].copy()
        label = f"OOS [{te_start}:{te_end if te_end else len(df)}]"

        pipe = fit_lasso(train, available)
        test["lasso_p_oos"] = predict_lasso(pipe, test, available, None)
        test["composite_oos"] = composite_score(test["v3_score"], test["lasso_p_oos"])
        oos_parts.append(test)

        # Overall IC
        rho_v3,  p_v3  = spearmanr(test["v3_score"],       test["r_realized"])
        rho_las, p_las = spearmanr(test["lasso_p_oos"],     test["r_realized"])
        rho_cmp, p_cmp = spearmanr(test["composite_oos"],   test["r_realized"])

        print(f"\n  {label}  (n={len(test)})")
        print(f"    v3.0 additive  IC={rho_v3:+.3f} (p={p_v3:.3f})")
        print(f"    Lasso OOS      IC={rho_las:+.3f} (p={p_las:.3f})")
        print(f"    Composite      IC={rho_cmp:+.3f} (p={p_cmp:.3f})")

        ic_rows.append({"window": label, "n": len(test),
                         "IC_v3": round(rho_v3, 3),
                         "IC_lasso_oos": round(rho_las, 3),
                         "IC_composite": round(rho_cmp, 3)})

        # Band stats
        bs = band_stats(test["composite_oos"], test["hit_target"], test["r_realized"], 80)
        band_rows.append({"window": label, **bs})
        print(f"    composite>=80: n={bs['n_band']}, win={bs['win_pct']}%, median_R={bs['median_r']}")

        # Per-regime IC
        for regime in ["risk_on", "neutral", "risk_off"]:
            sub = test[test["regime_label"] == regime]
            if len(sub) < 5:
                print(f"    [{regime}] n={len(sub)} — too few to score")
                continue
            rho_r, p_r = spearmanr(sub["composite_oos"], sub["r_realized"])
            bs_r = band_stats(sub["composite_oos"], sub["hit_target"], sub["r_realized"], 80)
            print(f"    [{regime:8s}] n={len(sub):4d}  IC={rho_r:+.3f} (p={p_r:.3f})"
                  f"  >=80: n={bs_r['n_band']}, win={bs_r['win_pct']}%")

    # -----------------------------------------------------------------------
    # Pooled OOS (all held-out trades together)
    # -----------------------------------------------------------------------
    oos = pd.concat(oos_parts)
    rho_pool, p_pool = spearmanr(oos["composite_oos"], oos["r_realized"])
    bs_pool = band_stats(oos["composite_oos"], oos["hit_target"], oos["r_realized"], 80)

    print(f"\n── Pooled OOS (n={len(oos)} trades across all windows) ──")
    print(f"  Composite IC = {rho_pool:+.3f}  (p={p_pool:.4f})")
    print(f"  Composite>=80: n={bs_pool['n_band']}, win={bs_pool['win_pct']}%, median_R={bs_pool['median_r']}")

    # -----------------------------------------------------------------------
    # Risk-off deep-dive
    # -----------------------------------------------------------------------
    print("\n── Risk-off regime deep-dive (ALL 24 trades, in-sample composite) ──")
    ro = df[df["regime_label"] == "risk_off"].copy()
    print(f"  n={len(ro)}  date range: {ro['entry_date'].min().date()} → {ro['entry_date'].max().date()}")
    print(f"  Trades per quarter:\n{ro.groupby(ro['entry_date'].dt.to_period('Q'))['entry_date'].count().to_string()}")

    if len(ro) >= 5:
        rho_ro, p_ro = spearmanr(ro["composite_is"], ro["r_realized"])
        print(f"  Composite IC (in-sample): {rho_ro:+.3f}  (p={p_ro:.3f})")
        print(f"  Win rate overall: {ro['hit_target'].mean():.1%}")
        print(f"  Win rate composite>=50: {ro[ro['composite_is']>=50]['hit_target'].mean():.1%}"
              f"  (n={( ro['composite_is']>=50).sum()})")
        print(f"  Win rate composite>=80: {ro[ro['composite_is']>=80]['hit_target'].mean():.1%}"
              f"  (n={(ro['composite_is']>=80).sum()})")
        print()
        print(f"  R-realized distribution: min={ro['r_realized'].min():.2f}  "
              f"median={ro['r_realized'].median():.2f}  max={ro['r_realized'].max():.2f}")
        print(f"  Exit reasons: {ro['exit_reason'].value_counts().to_dict()}")

    # -----------------------------------------------------------------------
    # Regime-conditional IC across full dataset (in-sample, for context)
    # -----------------------------------------------------------------------
    print("\n── Regime IC across full dataset (in-sample composite, for context) ──")
    for regime in ["risk_on", "neutral", "risk_off"]:
        sub = df[df["regime_label"] == regime]
        if len(sub) < 10:
            print(f"  [{regime:8s}] n={len(sub)} — insufficient for IC")
            continue
        rho_v, _ = spearmanr(sub["v3_score"],    sub["r_realized"])
        rho_l, _ = spearmanr(sub["lasso_p_is"],  sub["r_realized"])
        rho_c, _ = spearmanr(sub["composite_is"],sub["r_realized"])
        wr = sub["hit_target"].mean()
        print(f"  [{regime:8s}] n={len(sub):4d}  v3={rho_v:+.3f}  lasso={rho_l:+.3f}  "
              f"composite={rho_c:+.3f}  win_rate={wr:.1%}")

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    thresholds = {"strong": 0.15, "weak": 0.05}
    if abs(rho_pool) >= thresholds["strong"]:
        verdict = f"SIGNAL EXISTS: pooled OOS IC={rho_pool:+.3f} >= {thresholds['strong']} threshold"
    elif abs(rho_pool) >= thresholds["weak"]:
        verdict = f"WEAK SIGNAL:   pooled OOS IC={rho_pool:+.3f} (marginally above noise)"
    else:
        verdict = f"NO SIGNAL:     pooled OOS IC={rho_pool:+.3f} — composite is not predictive OOS"

    print(f"  {verdict}")
    n_ro = (df["regime_label"] == "risk_off").sum()
    print(f"  Risk-off coverage: {n_ro} trades — INSUFFICIENT for regime-specific conclusions.")
    print(f"  Model trained on 74.8% risk_on data. Do NOT use in sustained bear markets.")
    print()
    if bs_pool["n_band"] > 0:
        print(f"  Actionable threshold: composite >= 80 → {bs_pool['win_pct']}% win, "
              f"median R {bs_pool['median_r']} (n={bs_pool['n_band']} OOS trades)")


if __name__ == "__main__":
    main()
