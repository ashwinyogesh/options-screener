"""DITM IC audit v2 — technicals + lookahead-contaminated fundamentals proxy."""
import pandas as pd
from scipy.stats import spearmanr

df = pd.read_csv("ditm_backtest_augmented.csv")
print(f"n = {len(df)}\n")
print("⚠️  Fundamentals (fcf_yield, ev_ebitda, ps_ttm, roic_ttm, nd_ebitda) use")
print("    CURRENT 2026 snapshots — lookahead-contaminated. Treat ICs as upper bound.\n")

factors = [
    # Technicals (PIT-clean — from existing backtest)
    ("hv30", "tech"),
    ("iv_pct", "tech"),
    ("wk_rsi", "tech"),
    ("dist52w", "tech"),
    ("ret_200d", "tech"),
    ("trend_r2", "tech"),
    ("delta", "opt"),
    ("extrinsic_pct", "opt"),
    ("leverage", "opt"),
    # Composite scores (PIT-clean)
    ("env_score", "score"),
    ("strike_score", "score"),
    ("final_score", "score"),
    # Fundamentals (LOOKAHEAD)
    ("fcf_yield", "fund*"),
    ("ev_ebitda", "fund*"),
    ("ps_ttm", "fund*"),
    ("roic_ttm", "fund*"),
    ("nd_ebitda", "fund*"),
    # Macro/regime (PIT-clean — sector ETF returns at scan_date)
    ("sector_rs_6m", "macro"),
]


def verdict(rho, p):
    if p > 0.05:
        return "noise"
    if rho > 0.05:
        return "STRONG positive"
    if rho > 0.02:
        return "modest positive"
    if rho < -0.05:
        return "STRONG negative — invert"
    if rho < -0.02:
        return "modest negative"
    return "weak"


print("=== Spearman IC vs realised_roc_annualised (DITM 120d) ===")
print(f"{'factor':>16}  {'kind':>6}  {'rho':>8}  {'p-value':>10}   verdict")
print("-" * 80)
for col, kind in factors:
    if col not in df.columns:
        continue
    s = df.dropna(subset=[col, "realised_roc_annualised"])
    if len(s) < 50:
        continue
    rho, p = spearmanr(s[col], s["realised_roc_annualised"])
    print(f"{col:>16}  {kind:>6}  {rho:+.4f}   {p:.4g}   {verdict(rho, p)}")

print("\n=== Quintile mean ROC for the new fundamental factors ===")
for col in ["fcf_yield", "ev_ebitda", "ps_ttm", "roic_ttm", "nd_ebitda", "sector_rs_6m"]:
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

# Combined IC if we equal-weight the top-ranked factors (sanity check on potential lift)
print("\n=== Synthetic combined score (rank-equal-weight of selected factors) ===")
# Use: invert wk_rsi & dist52w (mis-signed), drop trend_r2, plus top fundamentals
keep_pos = ["leverage", "delta", "ret_200d", "fcf_yield", "roic_ttm"]
keep_neg = ["wk_rsi", "dist52w", "extrinsic_pct", "ev_ebitda", "nd_ebitda", "ps_ttm"]
# Higher rank = better. For neg factors, invert.
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
print(f"  combined-score IC (n={mask.sum()}):  rho = {rho:+.4f}  p = {p:.4g}")
print("  components +: " + ", ".join(keep_pos))
print("  components -: " + ", ".join(keep_neg))
