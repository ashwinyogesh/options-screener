"""
Test composite score combinations: v3.0 additive + Lasso P(target).
Run from repo root:  backend\\venv\\Scripts\\python.exe scripts\\composite_score_test.py
"""
import math, sys
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
sys.path.insert(0, "backend")
from services.scoring.swing_lasso import compute_swing_score_lasso

df = pd.read_csv("swing_backtest_universe_enriched.csv")
N = len(df)

# ── v3.0 additive ─────────────────────────────────────────────────────────
def rr_pts(rr):
    if rr <= 2.5: return 0.0
    if rr >= 3.0: return 40.0
    return 40.0 * (rr - 2.5) / 0.5
def macd_pts(m):
    if pd.isna(m) or m < 0: return 0.0
    if m >= 0.5: return 25.0
    if m >= 0.1: return 15.0 + (m - 0.1) / 0.4 * 10.0
    return 8.0 * m / 0.1
def bb_pts(b):
    if pd.isna(b) or b < 0: return 0.0
    if b >= 0.7: return 20.0
    if b >= 0.5: return 12.0 + (b - 0.5) / 0.2 * 8.0
    if b >= 0.3: return 4.0 + (b - 0.3) / 0.2 * 8.0
    return max(0.0, b * 13.0)
def vol_pts(v):
    if pd.isna(v): return 0.0
    if v >= 2.0: return 10.0
    if v >= 1.5: return 7.0 + (v - 1.5) / 0.5 * 3.0
    if v >= 1.2: return 4.0 + (v - 1.2) / 0.3 * 3.0
    return 0.0

df["v3"] = (
    (df["rr_planned"].apply(rr_pts)
     + df["setup_score"].apply(lambda s: min(30.0, s * 0.30))
     + df["macd_hist"].apply(macd_pts)
     + df["bb_pos"].apply(bb_pts)
     + df["vol_surge_20"].apply(vol_pts)
    ).clip(0, 100) * df["earnings_mult"]
).clip(0, 100)

# ── Lasso ──────────────────────────────────────────────────────────────────
FEATS = [
    "rr_planned","setup_score","adx_value","ad_line_slope_pct","higher_lows",
    "institutional_ownership_pct","extended","rsi14","macd_hist","atr_pct",
    "vol20","bb_pos","dist_sma20","dist_sma50","dist_sma200","pct_off_52w_high",
    "pct_above_52w_low","ret_1m","ret_3m","ret_6m","vol_surge_20","obv_slope_20",
    "base_depth","base_length","gap_up","inside_bar","nr7","spy_slope_50",
    "spy_ret_5d","vix_level","vix_vs_med20","rs_vs_spy_3m","log_price",
]
def run_lasso(row):
    feat = {k: row.get(k) for k in FEATS}
    feat["setup_breakout"]        = 1.0 if row.get("setup") == "breakout"  else 0.0
    feat["setup_momentum"]        = 1.0 if row.get("setup") == "momentum"  else 0.0
    feat["setup_reversion"]       = 1.0 if row.get("setup") == "reversion" else 0.0
    feat["regime_label_neutral"]  = 1.0 if row.get("regime_label") == "neutral"  else 0.0
    feat["regime_label_risk_off"] = 1.0 if row.get("regime_label") == "risk_off" else 0.0
    feat["regime_label_risk_on"]  = 1.0 if row.get("regime_label") == "risk_on"  else 0.0
    clean = {k: (float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None)
             for k, v in feat.items()}
    return compute_swing_score_lasso(clean)["p_target"] * 100

print("Computing Lasso scores …", flush=True)
df["lasso"] = [run_lasso(r) for r in df.to_dict("records")]
df["win"] = (df["r_realized"] >= 1.0).astype(int)

# ── rank-normalise both to [0,1] ───────────────────────────────────────────
df["v3_rank"]    = df["v3"].rank(pct=True)
df["lasso_rank"] = df["lasso"].rank(pct=True)

# ── candidate composites ───────────────────────────────────────────────────
df["c_equal"]    = (df["v3_rank"] + df["lasso_rank"]) / 2                       # equal-weight ranks
df["c_w60lasso"] = 0.40 * df["v3_rank"] + 0.60 * df["lasso_rank"]              # 60% Lasso (higher IC)
df["c_w70lasso"] = 0.30 * df["v3_rank"] + 0.70 * df["lasso_rank"]              # 70% Lasso
df["c_raw_avg"]  = (df["v3"] + df["lasso"]) / 2                                 # simple raw average
df["c_min"]      = df[["v3_rank","lasso_rank"]].min(axis=1)                     # most conservative: min
df["c_geomean"]  = np.sqrt(df["v3_rank"] * df["lasso_rank"])                    # geometric mean

# ── IC comparison ──────────────────────────────────────────────────────────
print()
print("=== Spearman IC vs r_realized ===")
cols = [
    ("v3",         "v3.0 additive (raw)"),
    ("lasso",      "Lasso P(target) (raw)"),
    ("c_equal",    "Composite: equal-weight ranks"),
    ("c_w60lasso", "Composite: 40% v3 + 60% Lasso ranks"),
    ("c_w70lasso", "Composite: 30% v3 + 70% Lasso ranks"),
    ("c_raw_avg",  "Composite: raw average"),
    ("c_min",      "Composite: min(ranks) — conservative"),
    ("c_geomean",  "Composite: geometric mean of ranks"),
]
for col, label in cols:
    s = df.dropna(subset=[col, "r_realized"])
    rho, p = spearmanr(s[col], s["r_realized"])
    print(f"  {label:<44}  rho = {rho:+.4f}  p={p:.2e}  n={len(s)}")

# ── win rate by quintile for best composite ────────────────────────────────
best = "c_equal"   # update after seeing IC results
print()
print(f"=== Win rate by quintile: {best} ===")
s = df.dropna(subset=[best, "win"]).copy()
s["q"] = pd.qcut(s[best], 5, labels=["Q1 (worst)", "Q2", "Q3", "Q4", "Q5 (best)"])
g = s.groupby("q", observed=True).agg(n=("win","count"), win_rate=("win","mean"), med_r=("r_realized","median"))
print(g.round(3).to_string())

# ── 5-point bands for best composite (scaled 0-100) ───────────────────────
print()
print("=== Best composite — trades by 5-pt band (scaled 0-100) ===")
df["best_scaled"] = df[best] * 100
CUTS   = list(range(0, 105, 5))
LABELS = [f"{i:3d}-{i+5:3d}" for i in range(0, 100, 5)]
df["band"] = pd.cut(df["best_scaled"], bins=CUTS, labels=LABELS, include_lowest=True)
g = df.groupby("band", observed=False).agg(n=("win","count"), w=("win","mean"), mr=("r_realized","median")).reset_index()
print(f"  {'Score':>7}  {'n':>4}  {'% total':>7}  {'win %':>5}  {'med R':>5}")
print("  " + "─" * 50)
cum = 0
for _, r in g.iterrows():
    if r["n"] == 0: continue
    cum += r["n"]
    print(f"  {r['band']:>7}  {int(r['n']):>4}  {r['n']/N*100:>6.1f}%  {r['w']*100:>4.0f}%  {r['mr']:>+5.2f}")
