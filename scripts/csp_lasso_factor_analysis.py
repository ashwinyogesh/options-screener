#!/usr/bin/env python3
"""
CSP Lasso Factor Analysis
=========================
Thinks as a CSP trader: assembles every factor that intuitively matters —
vol richness, moneyness / cushion, 52-week distance, RSI, trend, DTE, and
interaction terms — then uses LassoCV to discover which survive and at what
magnitude.

Produces three things:
  1. Coefficient importance table (Lasso + HV-neutralised Lasso + L1-Logistic)
  2. Decile backtest for the Lasso-derived score vs Method-D baseline
  3. Scoring implication summary

Usage (from repo root):
    backend\\venv\\Scripts\\python.exe scripts\\csp_lasso_factor_analysis.py
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import (
    Lasso,
    LassoCV,
    LinearRegression,
)
from sklearn.metrics import brier_score_loss, roc_auc_score  # noqa: F401 (kept for future use)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "csp_backtest_full_v2.csv"

SEP = "=" * 100
SEP2 = "-" * 100

# ─────────────────────────────────────────────────────────────────────────────
# 1.  LOAD
# ─────────────────────────────────────────────────────────────────────────────
df = pd.read_csv(CSV_PATH)
df["scan_date"] = pd.to_datetime(df["scan_date"])
df = df.sort_values("scan_date").reset_index(drop=True)

print(f"Loaded {len(df):,} trades  {df['scan_date'].min().date()} → {df['scan_date'].max().date()}")
print(f"  Tickers: {df['ticker'].nunique()}   Assignment rate: {df['assigned'].mean():.1%}")

# ─────────────────────────────────────────────────────────────────────────────
# 2.  FEATURE ENGINEERING  (every factor a CSP trader cares about)
# ─────────────────────────────────────────────────────────────────────────────

# --- Vol / premium richness --------------------------------------------------
# Selling puts is attractive when implied vol is elevated AND above realised vol.
df["hv30_pct"]         = df["hv30"] * 100                              # HV as %
df["iv_minus_hv"]      = df["iv_pct"] / 100 - df["hv30"]              # absolute vol premium (IV − HV)
df["iv_hv_ratio"]      = (df["iv_pct"] / 100) / df["hv30"].clip(0.01) # relative vol premium
df["annualised_yield"] = (                                             # raw put yield at entry
    df["premium"] / (df["strike"] - df["premium"]).clip(lower=0.01)
) * (365 / df["dte"]) * 100
df["premium_pct_spot"] = df["premium"] / df["spot"] * 100             # premium / spot (size-normalised)

# --- Moneyness / strike structure -------------------------------------------
# More OTM = more cushion = lower assignment risk but lower premium.
df["otm_pct"]   = (df["spot"] - df["strike"]) / df["spot"] * 100     # >0 = OTM
df["moneyness"] = df["strike"] / df["spot"]                           # k/S ratio; <1 = OTM put
df["delta_abs"] = df["delta"].abs()                                   # |Δ|; 0.20 typical for CSP
df["delta_sq"]  = df["delta_abs"] ** 2                                # nonlinear near-ATM assignment risk

# --- 52-week distance --------------------------------------------------------
# Method D found: stocks FAR from their 52W high give better CSP outcomes
# (they've already sold off — less downside continuation risk, richer IV).
df["dist52w_abs"]        = df["dist52w"].abs()                        # magnitude of distance below high
df["near_high_flag"]     = (df["dist52w_abs"] <= 5).astype(float)    # within 5 % of high
df["far_from_high_flag"] = (df["dist52w_abs"] >= 30).astype(float)   # >30 % below high

# --- SMA / trend momentum ---------------------------------------------------
# A stock above its SMA-50 is in an uptrend — less likely to collapse through a put strike.
df["sma_deviation"]   = (df["sma_ratio"] - 1.0) * 100               # % above/below SMA-50
df["sma_above"]       = (df["sma_ratio"] >= 1.0).astype(float)
df["sma50_slope_pct"] = df["sma50_slope_pct"]                        # SMA-50 slope (%/day)

# --- RSI --------------------------------------------------------------------
# Overbought stocks face reversal risk (bad for CSP seller); neutral RSI is safest.
df["rsi"]            = df["rsi"]
df["rsi_overbought"] = (df["rsi"] >= 70).astype(float)
df["rsi_oversold"]   = (df["rsi"] <= 30).astype(float)

# --- DTE --------------------------------------------------------------------
# Longer DTE = more premium collected but more time for the stock to move adversely.
# (dte already in df)

# --- Interaction terms -------------------------------------------------------
# Joint signals that a CSP trader intuitively combines:
df["iv_x_dist52w"] = df["iv_pct"] * df["dist52w_abs"]               # high IV + far from high (core CSP edge)
df["hv_x_delta"]   = df["hv30_pct"] * df["delta_abs"]               # vol × moneyness (joint assignment exposure)
df["yield_x_otm"]  = df["annualised_yield"] * df["otm_pct"]         # yield × cushion (ideal = both high)

# --- Current Method-D scorer components (for head-to-head comparison) -------
# env_SMA / env_SLP / env_RSI / env_OI are all zero in this dataset (Method D dropped them)
SCORER_COLS = ["env_IVP", "env_Tr", "strike_Delta", "strike_ROC"]

ALL_FEATURES = [
    # Vol / premium
    "hv30_pct", "iv_pct", "iv_minus_hv", "iv_hv_ratio",
    "annualised_yield", "premium_pct_spot",
    # Moneyness
    "otm_pct", "moneyness", "delta_abs", "delta_sq",
    # 52W distance
    "dist52w_abs", "near_high_flag", "far_from_high_flag",
    # SMA / momentum
    "sma_ratio", "sma_above", "sma_deviation", "sma50_slope_pct",
    # RSI
    "rsi", "rsi_overbought", "rsi_oversold",
    # DTE
    "dte",
    # Interactions
    "iv_x_dist52w", "hv_x_delta", "yield_x_otm",
    # Current scorer components
    *SCORER_COLS,
]

# Drop zero-variance columns (Method D zeroed out some factors → constant in data)
FEATURES = [c for c in ALL_FEATURES if df[c].std() > 1e-8]
dropped = set(ALL_FEATURES) - set(FEATURES)
if dropped:
    print(f"  Dropped constant columns (all zero in this dataset): {sorted(dropped)}")
print(f"  Feature count after dedup: {len(FEATURES)}")

# Direction note for each feature — what a positive coefficient means for CSP
NOTES: dict[str, str] = {
    "hv30_pct":          "+: Higher HV → higher raw premium yield (mechanical scaling)",
    "iv_pct":            "+: Higher IVP → richer options (seller's edge)",
    "iv_minus_hv":       "+: IV > HV → priced rich vs actual moves",
    "iv_hv_ratio":       "+: IV/HV > 1 → vol overpriced relative to realised vol",
    "annualised_yield":  "+: Higher raw yield (≈ outcome for non-assigned trades)",
    "premium_pct_spot":  "+: Premium richness normalised for stock price",
    "otm_pct":           "+: More OTM cushion → fewer assignments",
    "moneyness":         "-: k/S; lower = more OTM = better for CSP seller",
    "delta_abs":         "-: Higher |Δ| → near ATM → more assignment risk",
    "delta_sq":          "-: Nonlinear near-ATM assignment penalty",
    "dist52w_abs":       "+: Far from 52W high → Method D thesis (stock already sold off)",
    "near_high_flag":    "-: Near 52W high → reversal risk",
    "far_from_high_flag": "+: >30% below high → cushion for assignment recovery",
    "sma_ratio":         "?: Above SMA50 = uptrend (good) but could be overextended",
    "sma_above":         "+: Price > SMA50 → uptrend, stock less likely to collapse",
    "sma_deviation":     "+: Positive = above trend; very high = stretched (risky)",
    "sma50_slope_pct":   "+: Accelerating uptrend → momentum protective",
    "rsi":               "-: High RSI → overbought → reversal risk for CSP seller",
    "rsi_overbought":    "-: RSI ≥ 70 → elevated reversal risk",
    "rsi_oversold":      "?: RSI ≤ 30 → falling knife OR oversold bounce (ambiguous)",
    "dte":               "?: Longer DTE → more theta but more directional exposure",
    "iv_x_dist52w":      "+: High IV AND far from high = core joint CSP edge",
    "hv_x_delta":        "-: Joint realised vol × moneyness (combined assignment exposure)",
    "yield_x_otm":       "+: High yield WITH OTM cushion = ideal CSP setup",
    "env_IVP":           "Scorer: IVP component (Method D, max 60 pts)",
    "env_Tr":            "Scorer: Trend/52W component (Method D flipped, max 20 pts)",
    "strike_Delta":      "Scorer: Delta component (max 40 pts)",
    "strike_ROC":        "Scorer: ROC component (max 30 pts)",
}

# ─────────────────────────────────────────────────────────────────────────────
# 3.  PREPROCESSING
# ─────────────────────────────────────────────────────────────────────────────
X_raw = df[FEATURES].copy()
imp = SimpleImputer(strategy="median")
X_imp = pd.DataFrame(imp.fit_transform(X_raw), columns=FEATURES)
print(f"\n  Imputed: iv_pct={df['iv_pct'].isna().sum()} rows, "
      f"dist52w={df['dist52w'].isna().sum()} rows (replaced with column medians)")

# Winsorise target at 1st/99th percentile to reduce extreme-assignment leverage
p1, p99 = df["realised_roc_annualised"].quantile([0.01, 0.99])
y_cont = df["realised_roc_annualised"].clip(p1, p99).values
y_bin  = df["assigned"].astype(int).values

scaler = StandardScaler()
X_sc = scaler.fit_transform(X_imp)

tscv = TimeSeriesSplit(n_splits=5)

# ─────────────────────────────────────────────────────────────────────────────
# 4.  MODELS
# ─────────────────────────────────────────────────────────────────────────────
print("\nFitting LassoCV — ALL features (continuous: realised_roc_annualised) ...", end=" ", flush=True)
lasso = LassoCV(alphas=np.logspace(-4, 1, 80), cv=tscv, max_iter=20_000, random_state=42)
lasso.fit(X_sc, y_cont)
nz = int(np.sum(lasso.coef_ != 0))
print(f"done.  alpha={lasso.alpha_:.6f}   non-zero features: {nz}/{len(FEATURES)}")

# ─── CONDITIONAL LASSO: remove mechanically-dominant vol & yield features ──
# annualised_yield ≈ realised_roc for 78% of trades (non-assigned) by construction.
# hv30_pct and iv_pct also scale mechanically with premium. Removing these reveals
# the DISCRIMINATIVE factors — which setups beat expectations given their vol level.
MECH_FEATS = {"annualised_yield", "hv30_pct", "iv_pct", "premium_pct_spot",
              "iv_hv_ratio", "iv_minus_hv", "iv_x_dist52w", "yield_x_otm", "hv_x_delta"}
feat_cond     = [f for f in FEATURES if f not in MECH_FEATS]
idx_cond      = [i for i, f in enumerate(FEATURES) if f not in MECH_FEATS]
X_sc_cond     = X_sc[:, idx_cond]
print(f"Fitting LassoCV — CONDITIONAL (excluding {len(MECH_FEATS)} vol/yield drivers, {len(feat_cond)} features) ...",
      end=" ", flush=True)
lasso_cond = LassoCV(alphas=np.logspace(-4, 1, 80), cv=tscv, max_iter=20_000, random_state=42)
lasso_cond.fit(X_sc_cond, y_cont)
nz_cond = int(np.sum(lasso_cond.coef_ != 0))
print(f"done.  alpha={lasso_cond.alpha_:.6f}   non-zero: {nz_cond}/{len(feat_cond)}")

# Per-feature Spearman ρ vs assigned (fast assignment-risk perspective)
print("Computing assignment correlations ...", end=" ", flush=True)
assign_rho = {}
for i, f in enumerate(FEATURES):
    r, _ = spearmanr(X_imp[f], y_bin)
    assign_rho[f] = float(r) if not np.isnan(r) else 0.0
print("done.")

# Out-of-fold Spearman ρ for the primary Lasso model
oof_rho: list[float] = []
for tr, te in tscv.split(X_sc):
    m = Lasso(alpha=lasso.alpha_, max_iter=20_000).fit(X_sc[tr], y_cont[tr])
    r, _ = spearmanr(m.predict(X_sc[te]), y_cont[te])
    if not np.isnan(r):
        oof_rho.append(r)

# ─────────────────────────────────────────────────────────────────────────────
# 5.  HV-NEUTRALISED LASSO
#     Regress out hv30 (which mechanically inflates ROC on high-vol names) and
#     run Lasso on the residuals to find conditional factors.
# ─────────────────────────────────────────────────────────────────────────────
print("Fitting HV-neutralised Lasso (residuals after removing hv30 effect) ...",
      end=" ", flush=True)
hv_idx = FEATURES.index("hv30_pct")
hv_vec = X_sc[:, hv_idx : hv_idx + 1]
lr_hv = LinearRegression().fit(hv_vec, y_cont)
y_resid = y_cont - lr_hv.predict(hv_vec)

feat_no_hv  = [f for f in FEATURES if f != "hv30_pct"]
idx_no_hv   = [i for i, f in enumerate(FEATURES) if f != "hv30_pct"]
X_sc_no_hv  = X_sc[:, idx_no_hv]

lasso_hv = LassoCV(alphas=np.logspace(-4, 1, 80), cv=tscv, max_iter=20_000, random_state=42)
lasso_hv.fit(X_sc_no_hv, y_resid)
nz_hv = int(np.sum(lasso_hv.coef_ != 0))
print(f"done.  alpha={lasso_hv.alpha_:.6f}   non-zero: {nz_hv}/{len(feat_no_hv)}")

# ─────────────────────────────────────────────────────────────────────────────
# 6.  SCORES  (percentile-ranked to 0-100)
# ─────────────────────────────────────────────────────────────────────────────
df["lasso_raw"]       = lasso.predict(X_sc)
df["lasso_score"]     = df["lasso_raw"].rank(pct=True) * 100

df["lasso_hv_raw"]    = lasso_hv.predict(X_sc_no_hv) + lr_hv.predict(hv_vec).ravel()
df["lasso_hv_score"]  = df["lasso_hv_raw"].rank(pct=True) * 100

df["lasso_cond_raw"]  = lasso_cond.predict(X_sc_cond)
df["lasso_cond_score"]= df["lasso_cond_raw"].rank(pct=True) * 100

# ─────────────────────────────────────────────────────────────────────────────
# 7.  IC METRICS
# ─────────────────────────────────────────────────────────────────────────────
def overall_ic(score_col: str) -> tuple[float, float]:
    r, p = spearmanr(df[score_col], df["realised_roc_annualised"])
    return float(r), float(p)

def period_ic(score_col: str) -> tuple[float, float]:
    """Mean cross-sectional IC (per scan_date), ± std."""
    ics: list[float] = []
    for _, grp in df.groupby("scan_date"):
        if len(grp) >= 5:
            r, _ = spearmanr(grp[score_col], grp["realised_roc_annualised"])
            if not np.isnan(r):
                ics.append(r)
    arr = np.array(ics)
    return float(arr.mean()), float(arr.std())

ic_lasso,    pic_lasso    = overall_ic("lasso_score"),     period_ic("lasso_score")
ic_lasso_hv, pic_lasso_hv = overall_ic("lasso_hv_score"),  period_ic("lasso_hv_score")
ic_lasso_cond,pic_cond    = overall_ic("lasso_cond_score"), period_ic("lasso_cond_score")
ic_methodd,  pic_methodd  = overall_ic("final_score"),      period_ic("final_score")

# ─────────────────────────────────────────────────────────────────────────────
# 8.  DECILE BACKTEST
# ─────────────────────────────────────────────────────────────────────────────
def decile_table(score_col: str) -> pd.DataFrame:
    d = df.copy()
    d["_dec"] = pd.qcut(d[score_col], q=10,
                        labels=[f"D{i:02d}" for i in range(1, 11)],
                        duplicates="drop")
    rows = []
    for dec in [f"D{i:02d}" for i in range(10, 0, -1)]:   # D10 → D01
        sub = d[d["_dec"] == dec]
        if len(sub) == 0:
            continue
        r, _ = spearmanr(sub[score_col], sub["realised_roc_annualised"])
        rows.append({
            "Decile"      : dec,
            "Score Range" : f"{sub[score_col].min():.1f}–{sub[score_col].max():.1f}",
            "N Trades"    : len(sub),
            "Mean ROC %"  : round(sub["realised_roc_annualised"].mean(), 1),
            "Median ROC %": round(sub["realised_roc_annualised"].median(), 1),
            "Win Rate %"  : round((sub["assigned"] == 0).mean() * 100, 1),
            "Assign %"    : round(sub["assigned"].mean() * 100, 1),
            "Mean $ PnL"  : int(round(sub["pnl_per_contract"].mean())),
            "IC (ρ)"      : round(r, 3) if not np.isnan(r) else "—",
        })
    return pd.DataFrame(rows)

dec_lasso     = decile_table("lasso_score")
dec_lasso_hv  = decile_table("lasso_hv_score")
dec_lasso_cond= decile_table("lasso_cond_score")
dec_methodd   = decile_table("final_score")

# ─────────────────────────────────────────────────────────────────────────────
# 9.  COEFFICIENT TABLE
# ─────────────────────────────────────────────────────────────────────────────
hv_coef_map   = dict(zip(feat_no_hv, lasso_hv.coef_))
cond_coef_map = dict(zip(feat_cond,   lasso_cond.coef_))
coef_df = pd.DataFrame({
    "feature"      : FEATURES,
    "coef_lasso"   : lasso.coef_,
    "coef_hv_lasso": [hv_coef_map.get(f, np.nan) for f in FEATURES],
    "coef_cond"    : [cond_coef_map.get(f, np.nan) for f in FEATURES],
    "rho_assigned" : [assign_rho.get(f, np.nan) for f in FEATURES],
}).assign(
    abs_lasso = lambda d: d["coef_lasso"].abs(),
    zeroed    = lambda d: d["coef_lasso"] == 0,
    note      = lambda d: d["feature"].map(NOTES).fillna(""),
).sort_values("abs_lasso", ascending=False)

# ─────────────────────────────────────────────────────────────────────────────
# 10.  PRINT RESULTS
# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  CSP LASSO FACTOR ANALYSIS — COEFFICIENT TABLE  (all features, sorted by |coef|)")
print(SEP)
print(f"  LassoCV alpha  : {lasso.alpha_:.6f}   "
      f"non-zero: {nz}/{len(FEATURES)}   "
      f"OOF ρ: {np.mean(oof_rho):.3f} ± {np.std(oof_rho):.3f}")
print(f"  Conditional    : alpha={lasso_cond.alpha_:.6f}   non-zero: {nz_cond}/{len(feat_cond)}")
print(f"  Winsorised y   : [{p1:.1f}%, {p99:.1f}%]")
print()
print(f"  {'#':>3}  {'Feature':<22} {'Coef(all)':>10} {'Coef(HV-neut)':>14} {'Coef(Cond)':>11} {'\u03c1(assigned)':>12}  {'Z':>2}  Note")
print("  " + SEP2[:108])
for i, row in enumerate(coef_df.itertuples(), 1):
    z = "\u2717" if row.zeroed else " "
    hv_c = f"{row.coef_hv_lasso:+.3f}" if not np.isnan(row.coef_hv_lasso) else "  (ctrl)"
    cc_c = f"{row.coef_cond:+.3f}"    if not np.isnan(row.coef_cond)    else "  (excl)"
    ar   = f"{row.rho_assigned:+.3f}"
    print(
        f"  {i:>3}  {row.feature:<22} {row.coef_lasso:>+10.3f} {hv_c:>14} {cc_c:>11} {ar:>12}  {z:>2}  {row.note[:50]}"
    )

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  DECILE BACKTEST — LASSO SCORE (all features)  D10 = highest score = best")
print(f"  Overall IC : ρ={ic_lasso[0]:.3f} (p={ic_lasso[1]:.4f})")
print(f"  Period  IC : μ={pic_lasso[0]:.3f}  σ={pic_lasso[1]:.3f}  "
      f"IC/IR={pic_lasso[0]/pic_lasso[1]:.2f}" if pic_lasso[1] > 0 else "")
print(f"  Baseline   : Method D score  ρ={ic_methodd[0]:.3f}  period IC μ={pic_methodd[0]:.3f}")
print(SEP)
print(dec_lasso.to_string(index=False))

print()
print(SEP)
print("  DECILE BACKTEST — HV-NEUTRALISED LASSO  (factors beyond mechanical vol scaling)")
print(f"  Overall IC : ρ={ic_lasso_hv[0]:.3f}")
print(f"  Period  IC : μ={pic_lasso_hv[0]:.3f}  σ={pic_lasso_hv[1]:.3f}" if pic_lasso_hv[1] > 0 else "")
print(SEP)
print(dec_lasso_hv.to_string(index=False))

print()
print(SEP)
print("  DECILE BACKTEST — CONDITIONAL LASSO  (structural factors only: dist52w, delta, RSI, SMA, DTE)")
print(f"  Overall IC : ρ={ic_lasso_cond[0]:.3f}")
print(f"  Period  IC : μ={pic_cond[0]:.3f}  σ={pic_cond[1]:.3f}" if pic_cond[1] > 0 else "")
print(SEP)
print(dec_lasso_cond.to_string(index=False))

print()
print(SEP)
print("  DECILE BACKTEST — METHOD D final_score  (baseline comparison)")
print(f"  Overall IC : ρ={ic_methodd[0]:.3f}")
print(f"  Period  IC : μ={pic_methodd[0]:.3f}  σ={pic_methodd[1]:.3f}" if pic_methodd[1] > 0 else "")
print(SEP)
print(dec_methodd.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("  SCORING IMPLICATIONS")
print(SEP)
nonzero_ext = coef_df[
    (coef_df["coef_lasso"] != 0) &
    (~coef_df["feature"].isin(SCORER_COLS)) &
    (coef_df["abs_lasso"] > 1.0)
]
print("\n  Lasso-retained factors NOT in Method D scorer with |coef| > 1:")
for _, r in nonzero_ext.iterrows():
    print(f"    {r['feature']:<22}  coef={r['coef_lasso']:+.3f}   {r['note'][:65]}")

print("\n  Method D scorer components — Lasso verdict:")
for _, r in coef_df[coef_df["feature"].isin(SCORER_COLS)].iterrows():
    if r["coef_lasso"] == 0:
        verdict = "ZEROED by Lasso"
    else:
        verdict = f"retained  coef={r['coef_lasso']:+.3f}"
    hv_c = f"  (HV-neut: {r['coef_hv_lasso']:+.3f})" if not np.isnan(r["coef_hv_lasso"]) else ""
    print(f"    {r['feature']:<20} {verdict}{hv_c}")

# ─────────────────────────────────────────────────────────────────────────────
# 11.  SAVE
# ─────────────────────────────────────────────────────────────────────────────
coef_df.to_csv(REPO_ROOT / "csp_lasso_coefficients.csv", index=False)
dec_lasso.to_csv(REPO_ROOT / "csp_lasso_decile_results.csv", index=False)
dec_lasso_hv.to_csv(REPO_ROOT / "csp_lasso_hv_decile_results.csv", index=False)
dec_lasso_cond.to_csv(REPO_ROOT / "csp_lasso_cond_decile_results.csv", index=False)

print()
print(SEP)
print(f"[DONE]  n={len(df):,}   alpha={lasso.alpha_:.6f}")
print(f"  Lasso IC={ic_lasso[0]:.3f}   HV-neut IC={ic_lasso_hv[0]:.3f}   Cond IC={ic_lasso_cond[0]:.3f}   Method D IC={ic_methodd[0]:.3f}")
print(f"  OOF \u03c1 = {np.mean(oof_rho):.3f} \u00b1 {np.std(oof_rho):.3f}")
print(f"  Saved \u2192 csp_lasso_coefficients.csv, csp_lasso_decile_results.csv, csp_lasso_hv_decile_results.csv, csp_lasso_cond_decile_results.csv")
print(SEP)
