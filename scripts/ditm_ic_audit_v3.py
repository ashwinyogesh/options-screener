"""DITM IC audit v3 — TRUE PIT fundamentals from EDGAR companyfacts."""
import pandas as pd
from scipy.stats import spearmanr

df = pd.read_csv("ditm_backtest_pit.csv")
print(f"n = {len(df)}")
print(f"PIT lag (days from filing to scan_date): "
      f"median={df['_pit_lag_days'].median():.0f}, p25={df['_pit_lag_days'].quantile(0.25):.0f}, "
      f"p75={df['_pit_lag_days'].quantile(0.75):.0f}\n")

factors = [
    # Technicals
    ("hv30", "tech"),
    ("iv_pct", "tech"),
    ("wk_rsi", "tech"),
    ("dist52w", "tech"),
    ("ret_200d", "tech"),
    ("trend_r2", "tech"),
    ("delta", "opt"),
    ("extrinsic_pct", "opt"),
    ("leverage", "opt"),
    # Composite scores (current production)
    ("env_score", "score"),
    ("strike_score", "score"),
    ("final_score", "score"),
    # Fundamentals (PIT-clean)
    ("fcf_yield", "fund"),
    ("ev_ebitda", "fund"),
    ("ev_sales", "fund"),
    ("ps_ttm", "fund"),
    ("roic_ttm", "fund"),
    ("nd_ebitda", "fund"),
    ("debt_to_equity", "fund"),
    ("asset_turnover", "fund"),
    ("ni_margin", "fund"),
    ("op_margin", "fund"),
    # Macro
    ("sector_rs_6m", "macro"),
]


def verdict(rho, p):
    if p > 0.05:
        return "noise"
    if rho > 0.10:
        return "VERY STRONG +"
    if rho > 0.05:
        return "STRONG +"
    if rho > 0.02:
        return "modest +"
    if rho < -0.10:
        return "VERY STRONG - (invert)"
    if rho < -0.05:
        return "STRONG - (invert)"
    if rho < -0.02:
        return "modest -"
    return "weak"


print("=== Spearman IC vs realised_roc_annualised (DITM 120d, PIT-clean) ===")
print(f"{'factor':>16}  {'kind':>6}  {'rho':>8}  {'p-value':>10}   verdict")
print("-" * 80)
for col, kind in factors:
    if col not in df.columns:
        continue
    s = df.dropna(subset=[col, "realised_roc_annualised"])
    if len(s) < 100:
        continue
    rho, p = spearmanr(s[col], s["realised_roc_annualised"])
    print(f"{col:>16}  {kind:>6}  {rho:+.4f}   {p:.4g}   {verdict(rho, p)}  n={len(s)}")

print("\n=== Quintile mean ROC for top fundamental factors ===")
for col in ["fcf_yield", "ev_ebitda", "ev_sales", "ps_ttm", "roic_ttm", "ni_margin", "op_margin"]:
    if col not in df.columns:
        continue
    s = df.dropna(subset=[col, "realised_roc_annualised"]).copy()
    if len(s) < 100:
        continue
    try:
        s["q"] = pd.qcut(s[col], 5, labels=["Q1-low", "Q2", "Q3", "Q4", "Q5-high"], duplicates="drop")
    except Exception:
        continue
    g = s.groupby("q", observed=True)["realised_roc_annualised"].agg(["count", "mean", "median"]).round(1)
    print(f"\n{col}:")
    print(g.to_string())

print("\n=== Combined PIT score (rank-equal-weight of correctly-signed factors) ===")
keep_pos = ["leverage", "delta", "fcf_yield", "roic_ttm", "ni_margin", "op_margin", "asset_turnover"]
keep_neg = ["wk_rsi", "dist52w", "extrinsic_pct", "ev_ebitda", "ev_sales", "ps_ttm", "nd_ebitda", "debt_to_equity"]

ranks = pd.DataFrame(index=df.index)
for c in keep_pos:
    if c in df.columns:
        ranks[c] = df[c].rank(pct=True, na_option="keep")
for c in keep_neg:
    if c in df.columns:
        ranks[c] = (1 - df[c].rank(pct=True, na_option="keep"))
combo = ranks.mean(axis=1, skipna=True)
mask = combo.notna() & df["realised_roc_annualised"].notna()
rho, p = spearmanr(combo[mask], df.loc[mask, "realised_roc_annualised"])
print(f"  combined PIT IC (n={mask.sum()}):  rho = {rho:+.4f}  p = {p:.4g}")
print(f"  components +: {keep_pos}")
print(f"  components -: {keep_neg}")

# Decile analysis on the combined score
df_c = df.loc[mask].copy()
df_c["combo"] = combo[mask]
df_c["decile"] = pd.qcut(df_c["combo"], 10, labels=[f"D{i}" for i in range(1, 11)], duplicates="drop")
print("\n=== Combined PIT score: decile breakdown ===")
g = df_c.groupby("decile", observed=True)["realised_roc_annualised"].agg(
    ["count", "mean", "median"]
).round(1)
print(g.to_string())

# Also show how production score did on the same subset
prod_mask = mask & df["final_score"].notna()
rho_prod, _ = spearmanr(df.loc[prod_mask, "final_score"], df.loc[prod_mask, "realised_roc_annualised"])
print(f"\nProduction final_score IC on same subset (n={prod_mask.sum()}): rho = {rho_prod:+.4f}")
print(f"Combined PIT score on same subset: rho = {rho:+.4f}")
print(f"Lift: {rho - rho_prod:+.4f}")
