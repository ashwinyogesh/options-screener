"""DITM v4 score design — derive weights from PIT IC, build 0-100 score, show tiers.

Inputs: ditm_backtest_pit.csv (PIT-clean panel)
Output: prints proposed weights, score distribution, decile breakdown, tier bands.
"""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

df = pd.read_csv("ditm_backtest_pit.csv")
print(f"n = {len(df)}\n")

# --- Factor selection: keep only PIT-significant factors (p < 0.01) ---
# Sign convention: positive weight if IC > 0, negative if IC < 0.
# We'll rank-normalize each factor (0..1) and apply sign before weighting.

FACTORS = [
    # (column, sign, group, raw IC)
    # Valuation (PIT-strong)
    ("ps_ttm",         -1, "valuation", -0.1012),
    ("ev_sales",       -1, "valuation", -0.0897),
    ("ev_ebitda",      -1, "valuation", -0.0517),
    # Capital structure (regime-sensitive but significant)
    ("debt_to_equity", +1, "capital",   +0.0764),
    ("nd_ebitda",      +1, "capital",   +0.0420),
    # Technicals (PIT-clean, survived audit)
    ("wk_rsi",         -1, "technical", -0.0481),
    ("dist52w",        -1, "technical", -0.0272),
    ("hv30",           -1, "technical", -0.0292),
    ("ret_200d",       +1, "technical", +0.0214),
    # Macro
    ("sector_rs_6m",   -1, "macro",     -0.0439),
    # Option mechanics
    ("leverage",       +1, "option",    +0.0288),
    ("delta",          +1, "option",    +0.0245),
    ("extrinsic_pct",  -1, "option",    -0.0234),
]

# Weights proportional to |IC|. Normalize within group caps to avoid one group dominating.
GROUP_CAPS = {
    "valuation": 0.35,   # cap valuation at 35% of total (despite high IC)
    "capital":   0.15,
    "technical": 0.20,
    "macro":     0.05,
    "option":    0.25,   # option mechanics 25% (preserves DITM identity)
}

# Compute group-internal weights ∝ |IC|, then scale to GROUP_CAPS.
group_facs: dict[str, list] = {}
for col, sign, grp, ic in FACTORS:
    group_facs.setdefault(grp, []).append((col, sign, ic))

weights = {}
for grp, items in group_facs.items():
    abs_sum = sum(abs(ic) for _, _, ic in items)
    cap = GROUP_CAPS[grp]
    for col, sign, ic in items:
        weights[col] = sign * cap * (abs(ic) / abs_sum)

print("=== Proposed factor weights (sum of |w| = 1.00) ===")
print(f"{'factor':>16}  {'group':>10}  {'sign':>4}  {'weight':>7}  {'|w|':>5}")
for col, sign, grp, ic in FACTORS:
    w = weights[col]
    print(f"{col:>16}  {grp:>10}  {'+' if sign > 0 else '-':>4}  {w:+.3f}   {abs(w):.3f}")
print(f"  total |w| = {sum(abs(w) for w in weights.values()):.3f}")

# --- Compute score: rank-normalize each factor, weight, sum, scale 0..100 ---
def rank01(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, na_option="keep")

raw_score = pd.Series(0.0, index=df.index)
n_obs = pd.Series(0, index=df.index)
for col, sign, grp, ic in FACTORS:
    if col not in df.columns:
        continue
    r = rank01(df[col])
    # Impute missing factor contributions to cross-sectional median rank (0.5)
    # so absent data contributes a neutral, not a free-pass, signal.
    r_filled = r.fillna(0.5)
    contrib = weights[col] * r_filled  # weight already carries sign
    raw_score = raw_score.add(contrib, fill_value=0)
    n_obs = n_obs.add(r.notna().astype(int), fill_value=0)

# Only keep rows with at least 8 of the 13 factors observed (rest imputed)
mask = n_obs >= 8
df_v4 = df.loc[mask].copy()
df_v4["raw"] = raw_score[mask]

# Map raw to 0..100 by percentile so the score is interpretable like current
df_v4["score_v4"] = df_v4["raw"].rank(pct=True) * 100

print(f"\n=== Coverage: {mask.sum()}/{len(df)} rows scored ({100*mask.mean():.0f}%) ===")
print(f"\n=== score_v4 distribution ===")
print(df_v4["score_v4"].describe().round(1))

# IC of new score
rho_v4, p_v4 = spearmanr(df_v4["score_v4"], df_v4["realised_roc_annualised"])
rho_prod, _ = spearmanr(df.loc[mask, "final_score"], df.loc[mask, "realised_roc_annualised"])
print(f"\n=== IC comparison on n={mask.sum()} ===")
print(f"  Production final_score : rho = {rho_prod:+.4f}")
print(f"  New score_v4           : rho = {rho_v4:+.4f}")
print(f"  Lift                   : {rho_v4 - rho_prod:+.4f}")

# --- Decile breakdown ---
df_v4["decile"] = pd.qcut(df_v4["score_v4"], 10, labels=[f"D{i}" for i in range(1, 11)], duplicates="drop")
print("\n=== Decile breakdown of score_v4 vs forward 120d ROC ===")
g = df_v4.groupby("decile", observed=True).agg(
    n=("score_v4", "count"),
    score_min=("score_v4", "min"),
    score_max=("score_v4", "max"),
    mean_ROC=("realised_roc_annualised", "mean"),
    median_ROC=("realised_roc_annualised", "median"),
    win_pct=("pnl_per_contract", lambda x: (x > 0).mean() * 100),
).round(2)
print(g.to_string())

# --- Tier bands derived from deciles (mirror CC/CSP methodology) ---
print("\n=== Proposed tier bands (mirroring CC/CSP recalibration) ===")
# Use median ROC monotonicity to find natural cliffs.
# CC/CSP convention: A>=top10%, B>top30%, C>top50%, D>top70%, E<=bottom30%
p90 = df_v4["score_v4"].quantile(0.90)
p70 = df_v4["score_v4"].quantile(0.70)
p50 = df_v4["score_v4"].quantile(0.50)
p30 = df_v4["score_v4"].quantile(0.30)
print(f"  A (top 10%):     score >= {p90:.0f}")
print(f"  B (top 30%):     score >= {p70:.0f}")
print(f"  C (top 50%):     score >= {p50:.0f}")
print(f"  D (top 70%):     score >= {p30:.0f}")
print(f"  E (bottom 30%):  score <  {p30:.0f}")

# Show ROC by tier
df_v4["tier"] = pd.cut(
    df_v4["score_v4"],
    [-1, p30, p50, p70, p90, 101],
    labels=["E", "D", "C", "B", "A"],
)
print("\nTier-level outcomes:")
g2 = df_v4.groupby("tier", observed=True).agg(
    n=("score_v4", "count"),
    mean_ROC=("realised_roc_annualised", "mean"),
    median_ROC=("realised_roc_annualised", "median"),
    win_pct=("pnl_per_contract", lambda x: (x > 0).mean() * 100),
    mean_pnl=("pnl_per_contract", "mean"),
).round(1)
print(g2.to_string())

# --- A/E spread and monotonicity check ---
a_med = df_v4[df_v4["tier"] == "A"]["realised_roc_annualised"].median()
e_med = df_v4[df_v4["tier"] == "E"]["realised_roc_annualised"].median()
print(f"\nA-vs-E median ROC spread: {a_med - e_med:+.1f}pp")
print(f"  A median: {a_med:.1f}%   E median: {e_med:.1f}%")

# Sample top picks
print("\n=== Sample top-decile picks (D10) ===")
top = df_v4[df_v4["decile"] == "D10"].sort_values("score_v4", ascending=False).head(15)
print(top[["scan_date", "ticker", "spot", "score_v4", "ps_ttm", "ev_sales",
          "wk_rsi", "leverage", "realised_roc_annualised"]].round(2).to_string(index=False))

print("\n=== Sample bottom-decile picks (D1) ===")
bot = df_v4[df_v4["decile"] == "D1"].sort_values("score_v4").head(15)
print(bot[["scan_date", "ticker", "spot", "score_v4", "ps_ttm", "ev_sales",
          "wk_rsi", "leverage", "realised_roc_annualised"]].round(2).to_string(index=False))
