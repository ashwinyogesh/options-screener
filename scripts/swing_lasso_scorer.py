"""Lasso (L1) logistic regression + isotonic calibration on the enriched swing ledger.

Feeds ~35 candidate features (9 original + 26 augmented + one-hot setup/regime)
into LogisticRegressionCV with L1 penalty. Lasso zeroes out features that don't
help, leaving a self-pruned model. The surviving coefficients are then mapped
to calibrated win probabilities via IsotonicRegression on walk-forward OOS folds.

Outputs:
    - kept/dropped feature list with coefficients
    - reliability + score-band tables
    - scripts/out/swing_lasso_model.json
    - scripts/out/swing_lasso_oos.csv
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegressionCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "swing_backtest_universe_enriched.csv"
OUT_DIR = ROOT / "scripts" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- candidate feature menu ----------------------------------------------

ORIGINAL_NUMERIC = [
    "rr_planned",
    "setup_score",
    "adx_value",
    "ad_line_slope_pct",
    "higher_lows",
    "institutional_ownership_pct",
    "extended",  # bool → int
]

AUGMENTED_NUMERIC = [
    "rsi14",
    "macd_hist",
    "atr_pct",
    "vol20",
    "bb_pos",
    "dist_sma20",
    "dist_sma50",
    "dist_sma200",
    "pct_off_52w_high",
    "pct_above_52w_low",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "vol_surge_20",
    "obv_slope_20",
    "base_depth",
    "base_length",
    "gap_up",
    "inside_bar",
    "nr7",
    "spy_slope_50",
    "spy_ret_5d",
    "vix_level",
    "vix_vs_med20",
    "rs_vs_spy_3m",
    "log_price",
]

CATEGORICAL = ["setup", "regime_label"]


def _prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.tz_localize(None)
    df = df.sort_values("entry_date").reset_index(drop=True)
    df["is_win"] = (df["r_realized"] > 0).astype(int)
    df["extended"] = df["extended"].astype(int)

    # institutional_ownership_pct can be NaN → median impute (rare)
    if df["institutional_ownership_pct"].isna().any():
        df["institutional_ownership_pct"] = df["institutional_ownership_pct"].fillna(
            df["institutional_ownership_pct"].median()
        )

    # One-hot encode categoricals
    one_hot = pd.get_dummies(df[CATEGORICAL], drop_first=False).astype(int)
    df = pd.concat([df, one_hot], axis=1)

    features = ORIGINAL_NUMERIC + AUGMENTED_NUMERIC + list(one_hot.columns)
    # Drop any remaining NaN feature rows
    before = len(df)
    df = df.dropna(subset=features + ["is_win", "r_realized"]).reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"  Dropped {before - after} rows with missing features")
    return df, features


def walk_forward_oos(
    df: pd.DataFrame, features: list[str], n_splits: int = 5
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate out-of-sample raw probabilities via TimeSeriesSplit."""
    X = df[features].values.astype(float)
    y = df["is_win"].values

    oos_p = np.full(len(df), np.nan)
    oos_idx = np.full(len(df), False)

    tscv = TimeSeriesSplit(n_splits=n_splits)
    for fold, (tr, te) in enumerate(tscv.split(X), start=1):
        scaler = StandardScaler().fit(X[tr])
        Xtr = scaler.transform(X[tr])
        Xte = scaler.transform(X[te])
        # L1-penalised, CV over C (regularisation strength)
        model = LogisticRegressionCV(
            Cs=10,
            cv=5,
            penalty="l1",
            solver="saga",
            scoring="neg_log_loss",
            max_iter=4000,
            n_jobs=-1,
            random_state=0,
        )
        model.fit(Xtr, y[tr])
        p = model.predict_proba(Xte)[:, 1]
        oos_p[te] = p
        oos_idx[te] = True
        nz = int((model.coef_[0] != 0).sum())
        print(
            f"  fold {fold}: train n={len(tr):4d}  test n={len(te):4d}  "
            f"C={model.C_[0]:.4f}  non-zero coefs={nz}/{len(features)}"
        )

    mask = oos_idx
    return oos_p[mask], y[mask], np.where(mask)[0]


def reliability_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    bins = pd.qcut(p, n_bins, duplicates="drop")
    df = pd.DataFrame({"p": p, "y": y, "bin": bins})
    grp = df.groupby("bin", observed=True).agg(
        n=("y", "size"),
        p_pred=("p", "mean"),
        p_obs=("y", "mean"),
    )
    grp["calib_err"] = grp["p_pred"] - grp["p_obs"]
    return grp.round(4)


def score_band_table(df: pd.DataFrame, score_col: str = "score") -> pd.DataFrame:
    bins = [0, 30, 40, 50, 60, 70, 80, 90, 101]
    labels = ["0-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "90-100"]
    df = df.copy()
    df["band"] = pd.cut(df[score_col], bins=bins, labels=labels, right=False)
    grp = df.groupby("band", observed=True).agg(
        n=("is_win", "size"),
        win_rate=("is_win", "mean"),
        mean_R=("r_realized", "mean"),
        rr_planned=("rr_planned", "mean"),
    )
    grp["expected_R"] = grp["win_rate"] * grp["rr_planned"] - (1 - grp["win_rate"]) * 1.0
    return grp.round(3)


def coverage_table(df: pd.DataFrame, score_col: str = "score") -> pd.DataFrame:
    rows = []
    for floor in [50, 55, 60, 65, 70, 75, 80]:
        sub = df[df[score_col] >= floor]
        rows.append(
            {
                "floor": floor,
                "n": len(sub),
                "win_rate": sub["is_win"].mean() if len(sub) else np.nan,
                "mean_R": sub["r_realized"].mean() if len(sub) else np.nan,
                "sum_R": sub["r_realized"].sum() if len(sub) else 0.0,
            }
        )
    return pd.DataFrame(rows).round(3)


def main() -> int:
    print(f"Loading {LEDGER} ...")
    df = pd.read_csv(LEDGER)
    df, features = _prepare(df)
    print(
        f"Ledger: {len(df)} trades   "
        f"{df['entry_date'].min().date()} → {df['entry_date'].max().date()}   "
        f"base win rate = {df['is_win'].mean():.3f}"
    )
    print(f"Candidate feature menu: {len(features)} features")

    print("\n--- Walk-forward L1 logistic OOS ---")
    p_raw, y_oos, idx = walk_forward_oos(df, features, n_splits=5)
    print(f"Total OOS predictions: {len(p_raw)}")

    print("\n--- Reliability (raw L1 logistic, pre-calibration) ---")
    raw_tbl = reliability_table(p_raw, y_oos, n_bins=10)
    print(raw_tbl)
    brier_raw = float(np.mean((p_raw - y_oos) ** 2))
    print(f"  Brier (raw) = {brier_raw:.4f}")

    print("\n--- Fitting isotonic calibration on OOS predictions ---")
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p_raw, y_oos)
    p_cal = iso.transform(p_raw)
    brier_cal = float(np.mean((p_cal - y_oos) ** 2))
    print(f"  Brier (calibrated) = {brier_cal:.4f}  (delta {brier_cal-brier_raw:+.4f})")

    print("\n--- Reliability (calibrated) ---")
    cal_tbl = reliability_table(p_cal, y_oos, n_bins=10)
    print(cal_tbl)

    # Map back into df for score-band tables
    oos_df = df.iloc[idx].copy()
    oos_df["p_raw"] = p_raw
    oos_df["p_cal"] = p_cal
    oos_df["score"] = np.round(p_cal * 100).astype(int)

    print("\n--- Score band → empirical outcomes ---")
    band_tbl = score_band_table(oos_df)
    print(band_tbl)
    rho, _ = spearmanr(
        band_tbl.index.codes if hasattr(band_tbl.index, "codes") else range(len(band_tbl)),
        band_tbl["win_rate"].values,
    )
    print(f"  Win-rate monotonicity rho = {rho:+.3f}  (target > +0.90)")

    print("\n--- Coverage by score floor ---")
    print(coverage_table(oos_df))

    # ---- Final model: refit on full ledger ----
    print("\n--- FINAL model (refit on full ledger) ---")
    X = df[features].values.astype(float)
    y = df["is_win"].values
    scaler = StandardScaler().fit(X)
    final = LogisticRegressionCV(
        Cs=10,
        cv=5,
        penalty="l1",
        solver="saga",
        scoring="neg_log_loss",
        max_iter=6000,
        n_jobs=-1,
        random_state=0,
    )
    final.fit(scaler.transform(X), y)

    coefs = pd.Series(final.coef_[0], index=features).sort_values(
        key=lambda s: s.abs(), ascending=False
    )
    kept = coefs[coefs != 0]
    dropped = coefs[coefs == 0]
    print(f"\nFinal C = {final.C_[0]:.4f}")
    print(f"KEPT ({len(kept)}/{len(features)}):")
    for f, b in kept.items():
        print(f"  {f:32s} {b:+.4f}")
    print(f"\nDROPPED ({len(dropped)}/{len(features)}): {', '.join(dropped.index)}")
    print(f"\nintercept = {final.intercept_[0]:+.4f}")

    # Export
    model_payload = {
        "features": features,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_std": scaler.scale_.tolist(),
        "logreg_coef": final.coef_[0].tolist(),
        "logreg_intercept": float(final.intercept_[0]),
        "isotonic_x": iso.X_thresholds_.tolist(),
        "isotonic_y": iso.y_thresholds_.tolist(),
        "brier_raw": brier_raw,
        "brier_calibrated": brier_cal,
        "n_train": int(len(df)),
        "n_oos": int(len(p_raw)),
        "base_win_rate": float(df["is_win"].mean()),
    }
    model_path = OUT_DIR / "swing_lasso_model.json"
    model_path.write_text(json.dumps(model_payload, indent=2))
    print(f"\nModel → {model_path}")

    oos_df[
        ["symbol", "entry_date", "setup", "p_raw", "p_cal", "score", "is_win", "r_realized"]
    ].to_csv(OUT_DIR / "swing_lasso_oos.csv", index=False)
    print(f"OOS predictions → {OUT_DIR / 'swing_lasso_oos.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
