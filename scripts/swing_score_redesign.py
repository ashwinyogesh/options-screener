"""
Swing scorer redesign — find a composite that monotonically ranks realised R.

Loads the universe backtest ledger and evaluates several candidate scorers
on a held-out forward window. The criterion isn't max-rho — it's monotonic
decile means (top decile > 9 > 8 > ... > 1) on OUT-OF-SAMPLE data.

Why out-of-sample matters: with 14 features and 3,366 trades you can fit
*any* in-sample target. The OOS split kills lookback-fitting.

Pipeline:
  1. Load swing_backtest_universe.csv.
  2. Chronological 60/40 train/test split on entry_date.
  3. For each candidate scorer:
        - fit on TRAIN (closed-form weights for the linear ones)
        - apply to TEST
        - report Spearman ρ, win-rate by decile, mean R by decile,
          and a monotonicity score (Spearman of decile_index → mean_R)
  4. Print the winner.

Candidate scorers:
  c1  pure_rr           = rr_pts                                  (baseline)
  c2  rr_plus_setup     = rr_pts + 0.3 * setup_pts
  c3  linreg_all        = LinearRegression(all_features → r_realized)
  c4  logreg_win        = LogisticRegression(features → is_win)
  c5  per_setup_linreg  = separate LinearRegression per setup
  c6  current_final     = the existing final_score column         (reference)

Run from repo root:
  backend\\venv\\Scripts\\python.exe scripts\\swing_score_redesign.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "swing_backtest_universe.csv"

# Features available pre-trade (no leakage). Excludes exit_*, r_realized, days_held.
FEATURES: list[str] = [
    "rr_planned",
    "setup_score",
    "adx_value",
    "ad_line_slope_pct",
    "higher_lows",
    "institutional_ownership_pct",
    "regime_mult",
    "extended",            # bool
    "days_to_earnings",    # may be None → impute
]

POINT_BUCKETS: list[str] = ["rr_pts", "setup_pts", "ctx_pts", "inst_pts"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["extended"] = df["extended"].astype(int)
    # Impute: missing institutional ownership → median; missing dte → 999 (no earnings nearby)
    df["institutional_ownership_pct"] = df["institutional_ownership_pct"].fillna(
        df["institutional_ownership_pct"].median()
    )
    df["days_to_earnings"] = df["days_to_earnings"].fillna(999)
    df["adx_value"] = df["adx_value"].fillna(df["adx_value"].median())
    df["ad_line_slope_pct"] = df["ad_line_slope_pct"].fillna(0.0)
    df["higher_lows"] = df["higher_lows"].fillna(0)
    df["is_win"] = (df["exit_reason"] == "target").astype(int)
    return df


def _split(df: pd.DataFrame, train_frac: float = 0.6) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("entry_date").reset_index(drop=True)
    cut = int(len(df) * train_frac)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def _decile_table(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """Compute decile means + win rates for a score column on the given df."""
    df = df.copy()
    df["decile"] = pd.qcut(df[score_col], 10, labels=False, duplicates="drop") + 1
    out = df.groupby("decile").agg(
        n=("r_realized", "size"),
        score_lo=(score_col, "min"),
        score_hi=(score_col, "max"),
        mean_r=("r_realized", "mean"),
        median_r=("r_realized", "median"),
        win_rate=("is_win", "mean"),
    ).round(3)
    return out


def _monotonicity(dec: pd.DataFrame) -> float:
    """Spearman ρ between decile index (1..10) and mean R per decile.
    +1 means perfectly monotone increasing."""
    if len(dec) < 3:
        return float("nan")
    rho, _ = spearmanr(dec.index.values, dec["mean_r"].values)
    return float(rho)


def _evaluate(name: str, test: pd.DataFrame, scores: np.ndarray) -> dict:
    test = test.copy()
    test["score"] = scores
    rho, p = spearmanr(test["score"], test["r_realized"])
    dec = _decile_table(test, "score")
    mono = _monotonicity(dec)
    top = dec.iloc[-1]
    bot = dec.iloc[0]
    return {
        "name": name,
        "rho": float(rho),
        "p": float(p),
        "monotone": mono,
        "top_decile_mean_r": float(top["mean_r"]),
        "top_decile_win": float(top["win_rate"]),
        "bot_decile_mean_r": float(bot["mean_r"]),
        "bot_decile_win": float(bot["win_rate"]),
        "decile_table": dec,
    }


# ---------------------------------------------------------------------------
# Candidate scorers
# ---------------------------------------------------------------------------

def c1_pure_rr(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return test["rr_pts"].values


def c2_rr_plus_setup(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return (test["rr_pts"] + 0.3 * test["setup_pts"]).values


def c3_linreg_all(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, dict]:
    X_train = train[FEATURES].values.astype(float)
    y_train = train["r_realized"].values.astype(float)
    scaler = StandardScaler().fit(X_train)
    X_train_s = scaler.transform(X_train)
    model = LinearRegression().fit(X_train_s, y_train)
    X_test = scaler.transform(test[FEATURES].values.astype(float))
    coefs = dict(zip(FEATURES, model.coef_.round(3)))
    return model.predict(X_test), coefs


def c4_logreg_win(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, dict]:
    X_train = train[FEATURES].values.astype(float)
    y_train = train["is_win"].values.astype(int)
    scaler = StandardScaler().fit(X_train)
    X_train_s = scaler.transform(X_train)
    model = LogisticRegression(max_iter=2000).fit(X_train_s, y_train)
    X_test = scaler.transform(test[FEATURES].values.astype(float))
    coefs = dict(zip(FEATURES, model.coef_[0].round(3)))
    return model.predict_proba(X_test)[:, 1], coefs


def c5_per_setup_linreg(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, dict]:
    out = np.zeros(len(test))
    coefs_by_setup: dict[str, dict] = {}
    for setup in train["setup"].unique():
        tr = train[train["setup"] == setup]
        if len(tr) < 50:
            # fallback: rr_pts only
            mask = test["setup"] == setup
            out[mask.values] = test.loc[mask, "rr_pts"].values
            continue
        X_tr = tr[FEATURES].values.astype(float)
        y_tr = tr["r_realized"].values.astype(float)
        scaler = StandardScaler().fit(X_tr)
        model = LinearRegression().fit(scaler.transform(X_tr), y_tr)
        coefs_by_setup[setup] = dict(zip(FEATURES, model.coef_.round(3)))
        mask = test["setup"] == setup
        if mask.any():
            X_te = scaler.transform(test.loc[mask, FEATURES].values.astype(float))
            out[mask.values] = model.predict(X_te)
    return out, coefs_by_setup


def c6_current_final(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return test["final_score"].values


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not LEDGER.exists():
        sys.exit(f"Ledger not found: {LEDGER}. Run scripts/backtest_swing_universe.py first.")
    df = _prepare(pd.read_csv(LEDGER))
    train, test = _split(df, train_frac=0.6)
    print(f"Ledger : {len(df)} trades   {df['entry_date'].min().date()} → {df['entry_date'].max().date()}")
    print(f"Train  : {len(train)} trades   {train['entry_date'].min().date()} → {train['entry_date'].max().date()}")
    print(f"Test   : {len(test)} trades   {test['entry_date'].min().date()} → {test['entry_date'].max().date()}")

    results: list[dict] = []

    print("\n" + "=" * 78)
    print("c1: pure_rr  (baseline)")
    print("=" * 78)
    r = _evaluate("c1_pure_rr", test, c1_pure_rr(train, test)); results.append(r)
    print(r["decile_table"].to_string())
    print(f"  rho={r['rho']:+.3f}  monotone={r['monotone']:+.3f}  "
          f"top={r['top_decile_mean_r']:+.3f}R  bot={r['bot_decile_mean_r']:+.3f}R")

    print("\n" + "=" * 78)
    print("c2: rr_plus_setup  (rr_pts + 0.3 * setup_pts)")
    print("=" * 78)
    r = _evaluate("c2_rr_plus_setup", test, c2_rr_plus_setup(train, test)); results.append(r)
    print(r["decile_table"].to_string())
    print(f"  rho={r['rho']:+.3f}  monotone={r['monotone']:+.3f}  "
          f"top={r['top_decile_mean_r']:+.3f}R  bot={r['bot_decile_mean_r']:+.3f}R")

    print("\n" + "=" * 78)
    print("c3: linreg_all  (linear regression on all features, fit on train)")
    print("=" * 78)
    s, coefs = c3_linreg_all(train, test)
    r = _evaluate("c3_linreg_all", test, s); results.append(r)
    print("  coefficients (standardised):")
    for k, v in sorted(coefs.items(), key=lambda kv: -abs(kv[1])):
        print(f"    {k:<28} {v:+.3f}")
    print(r["decile_table"].to_string())
    print(f"  rho={r['rho']:+.3f}  monotone={r['monotone']:+.3f}  "
          f"top={r['top_decile_mean_r']:+.3f}R  bot={r['bot_decile_mean_r']:+.3f}R")

    print("\n" + "=" * 78)
    print("c4: logreg_win  (logistic on is_win, output = P(target))")
    print("=" * 78)
    s, coefs = c4_logreg_win(train, test)
    r = _evaluate("c4_logreg_win", test, s); results.append(r)
    print("  coefficients (standardised):")
    for k, v in sorted(coefs.items(), key=lambda kv: -abs(kv[1])):
        print(f"    {k:<28} {v:+.3f}")
    print(r["decile_table"].to_string())
    print(f"  rho={r['rho']:+.3f}  monotone={r['monotone']:+.3f}  "
          f"top={r['top_decile_mean_r']:+.3f}R  bot={r['bot_decile_mean_r']:+.3f}R")

    print("\n" + "=" * 78)
    print("c5: per_setup_linreg")
    print("=" * 78)
    s, coefs = c5_per_setup_linreg(train, test)
    r = _evaluate("c5_per_setup_linreg", test, s); results.append(r)
    for setup, c in coefs.items():
        print(f"  [{setup}] top coefs:")
        for k, v in sorted(c.items(), key=lambda kv: -abs(kv[1]))[:5]:
            print(f"    {k:<28} {v:+.3f}")
    print(r["decile_table"].to_string())
    print(f"  rho={r['rho']:+.3f}  monotone={r['monotone']:+.3f}  "
          f"top={r['top_decile_mean_r']:+.3f}R  bot={r['bot_decile_mean_r']:+.3f}R")

    print("\n" + "=" * 78)
    print("c6: current_final  (the live scorer — REFERENCE)")
    print("=" * 78)
    r = _evaluate("c6_current_final", test, c6_current_final(train, test)); results.append(r)
    print(r["decile_table"].to_string())
    print(f"  rho={r['rho']:+.3f}  monotone={r['monotone']:+.3f}  "
          f"top={r['top_decile_mean_r']:+.3f}R  bot={r['bot_decile_mean_r']:+.3f}R")

    # ---- Summary ----
    print("\n" + "=" * 78)
    print("SUMMARY (test set, n=%d)" % len(test))
    print("=" * 78)
    print(f"{'scorer':<22} {'rho':>8} {'monotone':>10} {'top R':>8} {'bot R':>8} {'spread':>8}")
    for r in results:
        spread = r["top_decile_mean_r"] - r["bot_decile_mean_r"]
        print(f"{r['name']:<22} {r['rho']:>+8.3f} {r['monotone']:>+10.3f} "
              f"{r['top_decile_mean_r']:>+8.3f} {r['bot_decile_mean_r']:>+8.3f} "
              f"{spread:>+8.3f}")

    best = max(results, key=lambda x: (x["monotone"] if x["monotone"] == x["monotone"] else -1,
                                       x["top_decile_mean_r"]))
    print(f"\nWinner by monotonicity: {best['name']}  "
          f"(monotone={best['monotone']:+.3f}, top decile R = {best['top_decile_mean_r']:+.3f})")


if __name__ == "__main__":
    main()
