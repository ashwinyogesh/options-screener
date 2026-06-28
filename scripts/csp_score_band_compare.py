"""
CSP Lasso vs Method D — by score band.
Recomputes lasso_score from raw features, then slices both scorers at
identical 10-point score bands for a direct apples-to-apples comparison.
"""
import warnings
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LassoCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
REPO_ROOT = Path(__file__).resolve().parents[1]

# ── load & feature engineering (mirrors csp_lasso_factor_analysis.py) ──────
df = pd.read_csv(REPO_ROOT / "csp_backtest_full_v2.csv")
df["scan_date"] = pd.to_datetime(df["scan_date"])
df = df.sort_values("scan_date").reset_index(drop=True)

df["hv30_pct"]           = df["hv30"] * 100
df["iv_minus_hv"]        = df["iv_pct"] / 100 - df["hv30"]
df["iv_hv_ratio"]        = (df["iv_pct"] / 100) / df["hv30"].clip(0.01)
df["annualised_yield"]   = (df["premium"] / (df["strike"] - df["premium"]).clip(lower=0.01)) * (365 / df["dte"]) * 100
df["premium_pct_spot"]   = df["premium"] / df["spot"] * 100
df["otm_pct"]            = (df["spot"] - df["strike"]) / df["spot"] * 100
df["moneyness"]          = df["strike"] / df["spot"]
df["delta_abs"]          = df["delta"].abs()
df["delta_sq"]           = df["delta_abs"] ** 2
df["dist52w_abs"]        = df["dist52w"].abs()
df["near_high_flag"]     = (df["dist52w_abs"] <= 5).astype(float)
df["far_from_high_flag"] = (df["dist52w_abs"] >= 30).astype(float)
df["sma_deviation"]      = (df["sma_ratio"] - 1.0) * 100
df["sma_above"]          = (df["sma_ratio"] >= 1.0).astype(float)
df["rsi_overbought"]     = (df["rsi"] >= 70).astype(float)
df["rsi_oversold"]       = (df["rsi"] <= 30).astype(float)
df["iv_x_dist52w"]       = df["iv_pct"] * df["dist52w_abs"]
df["hv_x_delta"]         = df["hv30_pct"] * df["delta_abs"]
df["yield_x_otm"]        = df["annualised_yield"] * df["otm_pct"]

FEATURES = [
    "hv30_pct", "iv_pct", "iv_minus_hv", "iv_hv_ratio",
    "annualised_yield", "premium_pct_spot",
    "otm_pct", "moneyness", "delta_abs", "delta_sq",
    "dist52w_abs", "near_high_flag", "far_from_high_flag",
    "sma_ratio", "sma_above", "sma_deviation", "sma50_slope_pct",
    "rsi", "rsi_overbought", "rsi_oversold", "dte",
    "iv_x_dist52w", "hv_x_delta", "yield_x_otm",
    "env_IVP", "env_Tr", "strike_Delta", "strike_ROC",
]
FEATURES = [c for c in FEATURES if df[c].std() > 1e-8]

X_imp = pd.DataFrame(
    SimpleImputer(strategy="median").fit_transform(df[FEATURES]), columns=FEATURES
)
X_sc = StandardScaler().fit_transform(X_imp)
p1, p99 = df["realised_roc_annualised"].quantile([0.01, 0.99])
y = df["realised_roc_annualised"].clip(p1, p99).values

print("Fitting LassoCV ...", end=" ", flush=True)
tscv = TimeSeriesSplit(n_splits=5)
lasso_m = LassoCV(alphas=np.logspace(-4, 1, 80), cv=tscv, max_iter=20_000, random_state=42)
lasso_m.fit(X_sc, y)
print(f"done. alpha={lasso_m.alpha_:.6f}")

df["lasso_score"] = pd.Series(lasso_m.predict(X_sc)).rank(pct=True) * 100

# ── score-band comparison ────────────────────────────────────────────────────
BANDS = [(90, 100), (80, 90), (70, 80), (60, 70), (50, 60),
         (40, 50),  (30, 40), (20, 30), (10, 20), (0,  10)]

def band_stats(sub: pd.DataFrame) -> dict:
    if len(sub) == 0:
        return dict(n=0, roc=float("nan"), med=float("nan"),
                    wr=float("nan"), asgn=float("nan"), pnl=float("nan"))
    return dict(
        n    = len(sub),
        roc  = sub["realised_roc_annualised"].mean(),
        med  = sub["realised_roc_annualised"].median(),
        wr   = (sub["assigned"] == 0).mean() * 100,
        asgn = sub["assigned"].mean() * 100,
        pnl  = sub["pnl_per_contract"].mean(),
    )

def mask(score_col: str, lo: int, hi: int) -> pd.DataFrame:
    if hi >= 100:
        return df[df[score_col] >= lo]
    return df[(df[score_col] >= lo) & (df[score_col] < hi)]

rows = []
for lo, hi in BANDS:
    sl = band_stats(mask("lasso_score", lo, hi))
    sm = band_stats(mask("final_score", lo, hi))
    rows.append(dict(band=f"{lo}-{hi}", **{f"L_{k}": v for k, v in sl.items()},
                                         **{f"M_{k}": v for k, v in sm.items()}))

t = pd.DataFrame(rows)

# ── print ────────────────────────────────────────────────────────────────────
SEP  = "=" * 120
SEP2 = "-" * 120

def r(v, fmt="+.1f", suffix=""):
    if v != v:   # nan
        return "—"
    return format(v, fmt) + suffix

print()
print(SEP)
print("  LASSO vs METHOD D — BY SCORE BAND  (both scored 0–100 percentile rank)")
print("  n=18,016 trades · 35 DTE · 154-ticker universe · 2024-01→2026-04")
print(SEP)

# Header
h1 = (f"  {'Band':>8}  "
      f"{'—— LASSO SCORE ————————————————————————————':45}  "
      f"{'—— METHOD D SCORE ——————————————————————————':45}")
h2 = (f"  {'Score':>8}  "
      f"{'N':>6}  {'MeanROC':>8}  {'Med ROC':>8}  {'Win Rate':>9}  {'Assign':>7}  {'$PnL':>7}  "
      f"  {'N':>6}  {'MeanROC':>8}  {'Med ROC':>8}  {'Win Rate':>9}  {'Assign':>7}  {'$PnL':>7}")
print(h1)
print(h2)
print("  " + SEP2[:116])

for _, row in t.iterrows():
    # Determine label
    lo = int(row["band"].split("-")[0])
    hi = int(row["band"].split("-")[1])
    if hi >= 100:
        label = "90–100"
    else:
        label = row["band"]

    # Mark the action zone (≥70 = "take it" for both scorers)
    tag = ""
    if lo >= 70:
        tag = " ◄ take"
    elif lo == 60:
        tag = " ◄ edge"

    def lf(k, fmt="+.1f", suf=""):
        return r(row[f"L_{k}"], fmt, suf)
    def mf(k, fmt="+.1f", suf=""):
        return r(row[f"M_{k}"], fmt, suf)

    l_n   = f"{int(row['L_n']):,}"  if row["L_n"] > 0 else "—"
    m_n   = f"{int(row['M_n']):,}"  if row["M_n"] > 0 else "—"

    print(f"  {label:>8}  "
          f"{l_n:>6}  {lf('roc'):>8}  {lf('med'):>8}  {lf('wr','.1f','%'):>9}  "
          f"{lf('asgn','.1f','%'):>7}  {lf('pnl','+.0f'):>7}  "
          f"  {m_n:>6}  {mf('roc'):>8}  {mf('med'):>8}  {mf('wr','.1f','%'):>9}  "
          f"{mf('asgn','.1f','%'):>7}  {mf('pnl','+.0f'):>7}{tag}")

print()
print("  Win Rate = put expires worthless (not assigned).  $PnL = mean per 100-share contract.")
print("  Score = percentile rank (0=worst, 100=best) for both scorers.")
print(SEP)

# ── IC by band ───────────────────────────────────────────────────────────────
print()
print("  IC (Spearman ρ of score vs realised ROC) WITHIN EACH BAND")
print(f"  {'Band':>8}  {'Lasso IC':>10}  {'MethodD IC':>12}  {'Lasso N':>9}  {'MD N':>9}")
print("  " + "-" * 55)
for _, row in t.iterrows():
    label = row["band"] if int(row["band"].split("-")[1]) < 100 else "90–100"
    lo = int(row["band"].split("-")[0])
    hi = int(row["band"].split("-")[1])
    sl = mask("lasso_score", lo, hi)
    sm = mask("final_score", lo, hi)
    rl = spearmanr(sl["lasso_score"], sl["realised_roc_annualised"]).statistic if len(sl) > 5 else float("nan")
    rm = spearmanr(sm["final_score"], sm["realised_roc_annualised"]).statistic if len(sm) > 5 else float("nan")
    print(f"  {label:>8}  {r(rl,'+.3f'):>10}  {r(rm,'+.3f'):>12}  {len(sl):>9,}  {len(sm):>9,}")

# Overall ICs
roc_col = "realised_roc_annualised"
lic = spearmanr(df["lasso_score"], df[roc_col]).statistic
mic = spearmanr(df["final_score"], df[roc_col]).statistic
print()
print(f"  {'OVERALL':>8}  {lic:>+10.3f}  {mic:>+12.3f}  {len(df):>9,}  {len(df):>9,}")
print()
