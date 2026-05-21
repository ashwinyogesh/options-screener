"""IC audit for CC and CSP panels with EDGAR PIT fundamentals.

Usage:
    python scripts/cc_csp_ic_audit.py cc_backtest_pit.csv
    python scripts/cc_csp_ic_audit.py csp_backtest_pit.csv
"""
import sys
import pandas as pd
from scipy.stats import spearmanr

PATH = sys.argv[1]
df = pd.read_csv(PATH)
label = "CC" if "cc_" in PATH else "CSP"
print(f"=== {label} :: {PATH} :: n={len(df)} ===")
print(f"PIT lag: median={df['_pit_lag_days'].median():.0f}d "
      f"(p25={df['_pit_lag_days'].quantile(0.25):.0f}, "
      f"p75={df['_pit_lag_days'].quantile(0.75):.0f})\n")

factors = [
    # Raw technical inputs
    ("hv30", "tech"),
    ("iv_pct", "tech"),
    ("rsi", "tech"),
    ("sma_ratio", "tech"),
    ("sma50_slope_pct", "tech"),
    ("dist52w", "tech"),
    ("delta", "opt"),
    # Production sub-scores
    ("env_IVP", "subscore"),
    ("env_Tr", "subscore"),
    ("env_SMA", "subscore"),
    ("env_SLP", "subscore"),
    ("env_RSI", "subscore"),
    ("env_OI", "subscore"),
    ("strike_Delta", "subscore"),
    ("strike_ROC", "subscore"),
    ("env_score", "score"),
    ("strike_quant_score", "score"),
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


def audit_target(target: str):
    print(f"\n=== Spearman IC vs {target} ===")
    print(f"{'factor':>20}  {'kind':>9}  {'rho':>8}  {'p-value':>10}   verdict")
    print("-" * 88)
    out = []
    for col, kind in factors:
        if col not in df.columns:
            continue
        s = df.dropna(subset=[col, target])
        if len(s) < 100 or s[col].nunique() < 5:
            continue
        rho, p = spearmanr(s[col], s[target])
        out.append((col, kind, rho, p, len(s)))
        print(f"{col:>20}  {kind:>9}  {rho:+.4f}   {p:.4g}   {verdict(rho, p)}  n={len(s)}")
    return out


# Two targets matter for premium-selling strategies:
#   realised_roc_annualised  ->  full dollar economics including assignment outcomes
#   pnl_per_contract         ->  raw PnL incl. assignment
audit_target("realised_roc_annualised")
audit_target("pnl_per_contract")

# --- Composite PIT score: rank-EW with sign discipline ---
# For premium-selling: we want to AVOID drawdowns at expiry (assignment far ITM)
# and want stable underlyings. Direction may differ from DITM.
print("\n=== Composite PIT score (rank-EW, signs from IC table) ===")
ic_table = audit_target("realised_roc_annualised")
sig_pos = [c for c, k, r, p, n in ic_table if k == "fund" and r > 0.02 and p < 0.05]
sig_neg = [c for c, k, r, p, n in ic_table if k == "fund" and r < -0.02 and p < 0.05]
print(f"  + components: {sig_pos}")
print(f"  - components: {sig_neg}")

if sig_pos or sig_neg:
    ranks = pd.DataFrame(index=df.index)
    for c in sig_pos:
        ranks[c] = df[c].rank(pct=True, na_option="keep")
    for c in sig_neg:
        ranks[c] = 1 - df[c].rank(pct=True, na_option="keep")
    combo = ranks.mean(axis=1, skipna=True)
    mask = combo.notna() & df["realised_roc_annualised"].notna()
    if mask.sum() > 100:
        rho_combo, _ = spearmanr(combo[mask], df.loc[mask, "realised_roc_annualised"])
        rho_prod, _ = spearmanr(
            df.loc[mask, "final_score"], df.loc[mask, "realised_roc_annualised"]
        )
        print(f"\n  Production final_score IC (n={mask.sum()}): rho = {rho_prod:+.4f}")
        print(f"  PIT-only composite IC                  : rho = {rho_combo:+.4f}")
        print(f"  Pure-fundamentals lift over production : {rho_combo - rho_prod:+.4f}")

        # Now blend: production final_score + PIT composite, equal-rank
        prod_rank = df.loc[mask, "final_score"].rank(pct=True)
        pit_rank = combo[mask].rank(pct=True)
        blend = 0.5 * prod_rank + 0.5 * pit_rank
        rho_blend, _ = spearmanr(blend, df.loc[mask, "realised_roc_annualised"])
        print(f"  50/50 blend (prod + PIT)               : rho = {rho_blend:+.4f}")
        print(f"  Lift of blend over production          : {rho_blend - rho_prod:+.4f}")

        # Decile breakdown of the blend
        d = df.loc[mask].copy()
        d["blend"] = blend
        d["decile"] = pd.qcut(
            d["blend"], 10, labels=[f"D{i}" for i in range(1, 11)], duplicates="drop"
        )
        print("\n  Decile breakdown of blend vs realised_roc_annualised:")
        g = d.groupby("decile", observed=True).agg(
            n=("blend", "size"),
            mean_ROC=("realised_roc_annualised", "mean"),
            median_ROC=("realised_roc_annualised", "median"),
            win_pct=("pnl_per_contract", lambda s: (s > 0).mean() * 100),
        ).round(2)
        print(g.to_string())
