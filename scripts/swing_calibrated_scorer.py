"""
Swing screener — calibrated probability scorer.

Goal: produce score ∈ [0, 100] = round(100 × P(target_hit)), such that score=70
empirically wins ~70% of the time. The score then directly answers the user's
question: "what's the probability this setup works?"

Pipeline:
  1. Load swing_backtest_universe.csv ledger.
  2. Walk-forward CV (TimeSeriesSplit, 5 folds) on entry_date order:
       - Each fold: train logistic regression on past, predict on future.
       - Pool out-of-sample probabilities.
  3. Fit IsotonicRegression on OOS predictions → calibration map.
  4. Final pipeline = LogReg (refit on full data) + isotonic calibration.
  5. Report:
       - Reliability table (predicted vs observed by score bucket)
       - Win-rate by score decile (monotonicity check)
       - Expected R by score band (40-50, 50-60, … 80-90, 90+)
       - Feature coefficients
       - Brier score, log loss
  6. Export production constants (Python dict) → drop into
     services/scoring/swing.py.

Run from repo root:
  backend\\venv\\Scripts\\python.exe scripts\\swing_calibrated_scorer.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "swing_backtest_universe.csv"

FEATURES: list[str] = [
    "rr_planned",
    "setup_score",
    "adx_value",
    "ad_line_slope_pct",
    "higher_lows",
    "institutional_ownership_pct",
    "regime_mult",
    "extended",
    "days_to_earnings",
]


# ---------------------------------------------------------------------------
# Data prep
# ---------------------------------------------------------------------------

def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["extended"] = df["extended"].astype(int)
    df["institutional_ownership_pct"] = df["institutional_ownership_pct"].fillna(
        df["institutional_ownership_pct"].median()
    )
    df["days_to_earnings"] = df["days_to_earnings"].fillna(999)
    df["adx_value"] = df["adx_value"].fillna(df["adx_value"].median())
    df["ad_line_slope_pct"] = df["ad_line_slope_pct"].fillna(0.0)
    df["higher_lows"] = df["higher_lows"].fillna(0)
    df["is_win"] = (df["exit_reason"] == "target").astype(int)
    return df.sort_values("entry_date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Walk-forward OOS probability collection
# ---------------------------------------------------------------------------

def walk_forward_oos(df: pd.DataFrame, n_splits: int = 5) -> pd.DataFrame:
    """TimeSeriesSplit. Returns df rows from the test folds with `p_raw` filled."""
    X = df[FEATURES].values.astype(float)
    y = df["is_win"].values.astype(int)
    tss = TimeSeriesSplit(n_splits=n_splits)

    out = df.iloc[0:0].copy()
    out["p_raw"] = pd.Series(dtype=float)

    for fold, (tr_idx, te_idx) in enumerate(tss.split(X), 1):
        scaler = StandardScaler().fit(X[tr_idx])
        model = LogisticRegression(max_iter=2000, C=1.0).fit(scaler.transform(X[tr_idx]), y[tr_idx])
        p = model.predict_proba(scaler.transform(X[te_idx]))[:, 1]
        fold_df = df.iloc[te_idx].copy()
        fold_df["p_raw"] = p
        fold_df["fold"] = fold
        out = pd.concat([out, fold_df], ignore_index=True)
        tr_dates = df["entry_date"].iloc[tr_idx]
        te_dates = df["entry_date"].iloc[te_idx]
        print(f"  fold {fold}: train n={len(tr_idx):>5} "
              f"({tr_dates.min().date()} → {tr_dates.max().date()})  "
              f"test n={len(te_idx):>4} "
              f"({te_dates.min().date()} → {te_dates.max().date()})")
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def reliability_table(df: pd.DataFrame, prob_col: str, bins: int = 10) -> pd.DataFrame:
    df = df.copy()
    df["bucket"] = pd.qcut(df[prob_col], bins, labels=False, duplicates="drop") + 1
    out = df.groupby("bucket").agg(
        n=("is_win", "size"),
        p_predicted_mean=(prob_col, "mean"),
        p_observed=("is_win", "mean"),
        mean_R=("r_realized", "mean"),
    ).round(4)
    out["calib_error"] = (out["p_predicted_mean"] - out["p_observed"]).round(4)
    return out


def score_band_table(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    edges = [0, 30, 40, 50, 60, 70, 80, 90, 100]
    labels = ["0-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "90-100"]
    df = df.copy()
    df["band"] = pd.cut(df[score_col], edges, labels=labels, include_lowest=True)
    out = df.groupby("band", observed=True).agg(
        n=("is_win", "size"),
        win_rate=("is_win", "mean"),
        mean_R=("r_realized", "mean"),
        median_R=("r_realized", "median"),
        rr_planned=("rr_planned", "mean"),
    ).round(3)
    # expected_R from win_rate × mean rr_planned − (1−win_rate)
    out["expected_R"] = (out["win_rate"] * out["rr_planned"] - (1 - out["win_rate"])).round(3)
    return out


def monotonicity(table: pd.DataFrame, key: str) -> float:
    if len(table) < 3:
        return float("nan")
    rho, _ = spearmanr(np.arange(len(table)), table[key].values)
    return float(rho)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not LEDGER.exists():
        sys.exit(f"Ledger not found: {LEDGER}")
    df = _prepare(pd.read_csv(LEDGER))
    print(f"Ledger: {len(df)} trades   "
          f"{df['entry_date'].min().date()} → {df['entry_date'].max().date()}   "
          f"base win rate = {df['is_win'].mean():.3f}")

    print("\n--- Walk-forward OOS probability collection (5 folds) ---")
    oos = walk_forward_oos(df, n_splits=5)
    print(f"\nTotal OOS predictions: {len(oos)}")

    # Calibrate raw OOS probabilities → empirical hit rate
    print("\n--- Fitting isotonic calibration on OOS predictions ---")
    iso = IsotonicRegression(out_of_bounds="clip").fit(oos["p_raw"].values, oos["is_win"].values)
    oos["p_calibrated"] = iso.transform(oos["p_raw"].values)
    oos["score"] = (oos["p_calibrated"] * 100).round().astype(int)

    # ----- Calibration quality -----
    print("\n--- Reliability (raw logistic, pre-calibration) ---")
    rel_raw = reliability_table(oos, "p_raw", bins=10)
    print(rel_raw.to_string())
    brier_raw = brier_score_loss(oos["is_win"], oos["p_raw"])
    ll_raw = log_loss(oos["is_win"], oos["p_raw"].clip(1e-6, 1 - 1e-6))
    print(f"  Brier = {brier_raw:.4f}   log-loss = {ll_raw:.4f}")

    print("\n--- Reliability (calibrated, post-isotonic) ---")
    rel_cal = reliability_table(oos, "p_calibrated", bins=10)
    print(rel_cal.to_string())
    brier_cal = brier_score_loss(oos["is_win"], oos["p_calibrated"])
    ll_cal = log_loss(oos["is_win"], oos["p_calibrated"].clip(1e-6, 1 - 1e-6))
    print(f"  Brier = {brier_cal:.4f}   log-loss = {ll_cal:.4f}")
    print(f"  Calibration error (mean abs): {rel_cal['calib_error'].abs().mean():.4f}")

    # ----- Monotonicity & expected R -----
    print("\n--- Score band → empirical outcomes (the screener UX view) ---")
    bands = score_band_table(oos, "score")
    print(bands.to_string())
    mono = monotonicity(bands, "win_rate")
    print(f"  Win-rate monotonicity across score bands: rho = {mono:+.3f}  (target: > +0.90)")

    # ----- Coverage if user trades only score ≥ N -----
    print("\n--- Coverage / expectancy by score floor ---")
    print(f"{'floor':>6} {'n':>6} {'win_rate':>9} {'mean_R':>8} {'sum_R':>8}")
    for floor in [50, 55, 60, 65, 70, 75, 80]:
        sub = oos[oos["score"] >= floor]
        if len(sub) == 0:
            continue
        print(f"{floor:>6} {len(sub):>6} {sub['is_win'].mean():>9.3f} "
              f"{sub['r_realized'].mean():>+8.3f} {sub['r_realized'].sum():>+8.1f}")

    # ----- Refit final model on ALL data + show coefficients -----
    print("\n--- FINAL model (refit on full ledger) ---")
    X = df[FEATURES].values.astype(float)
    y = df["is_win"].values.astype(int)
    scaler = StandardScaler().fit(X)
    final_model = LogisticRegression(max_iter=2000, C=1.0).fit(scaler.transform(X), y)
    coefs = dict(zip(FEATURES, final_model.coef_[0]))
    intercept = float(final_model.intercept_[0])
    means = dict(zip(FEATURES, scaler.mean_))
    stds = dict(zip(FEATURES, scaler.scale_))

    print("\nstandardised coefficients (sorted |coef|):")
    for k, v in sorted(coefs.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {k:<28} {v:+.4f}")
    print(f"intercept = {intercept:+.4f}")

    # Final isotonic re-fit on full data using a clean 5-fold OOS regeneration
    # (oos is already from walk-forward; reuse its calibration since it's OOS)
    iso_knots = np.column_stack([iso.X_thresholds_, iso.y_thresholds_])
    print(f"\nIsotonic calibration knots: {len(iso_knots)} points")
    print("  raw_p → calibrated_p")
    # Print a few sample mappings the production code can interpolate from
    sample_probs = np.linspace(0.05, 0.95, 19)
    print("  raw  →  cal  →  score")
    for p in sample_probs:
        cal = float(iso.transform([p])[0])
        print(f"  {p:.2f} → {cal:.3f} → {int(round(cal * 100))}")

    # ----- Export production constants -----
    out_dir = REPO_ROOT / "scripts" / "out"
    out_dir.mkdir(exist_ok=True)
    export = {
        "features": FEATURES,
        "scaler_mean": means,
        "scaler_std": stds,
        "logreg_coef": dict(coefs),
        "logreg_intercept": intercept,
        "isotonic_x": iso.X_thresholds_.tolist(),
        "isotonic_y": iso.y_thresholds_.tolist(),
        "metrics": {
            "brier_raw": brier_raw,
            "brier_calibrated": brier_cal,
            "log_loss_raw": ll_raw,
            "log_loss_calibrated": ll_cal,
            "win_rate_band_monotonicity": mono,
            "n_trades": int(len(df)),
            "base_win_rate": float(df["is_win"].mean()),
            "date_range": [str(df["entry_date"].min().date()), str(df["entry_date"].max().date())],
        },
    }
    out_path = out_dir / "swing_calibrated_model.json"
    with open(out_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\nModel constants exported → {out_path}")

    # Also save OOS predictions for further analysis
    oos_path = out_dir / "swing_oos_predictions.csv"
    oos[["symbol", "entry_date", "setup", "rr_planned", "is_win",
         "r_realized", "p_raw", "p_calibrated", "score"]].to_csv(oos_path, index=False)
    print(f"OOS predictions → {oos_path}")


if __name__ == "__main__":
    main()
