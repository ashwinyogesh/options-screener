"""DITM IC audit — per-factor Spearman correlation vs forward outcomes."""
import pandas as pd
from scipy.stats import spearmanr

df = pd.read_csv('ditm_backtest_full.csv')
print(f"n = {len(df)}\n")

factors = [
    ('hv30', 'raw HV30 (vol level)'),
    ('iv_pct', 'IV percentile'),
    ('wk_rsi', 'weekly RSI'),
    ('dist52w', 'dist from 52w high (% — neg=below)'),
    ('ret_200d', '200-day return'),
    ('trend_r2', 'R² of trend regression'),
    ('delta', 'option delta'),
    ('extrinsic_pct', 'extrinsic % of premium'),
    ('leverage', 'effective leverage'),
    ('env_score', 'composite env score'),
    ('strike_score', 'composite strike score'),
    ('final_score', 'final blended score'),
]

def verdict(rho, p):
    if p > 0.05:
        return 'noise'
    if rho > 0.05:
        return 'STRONG positive — keep, weight up'
    if rho > 0.02:
        return 'modest positive — keep'
    if rho < -0.05:
        return 'STRONG negative — INVERT'
    if rho < -0.02:
        return 'modest negative — invert or drop'
    return 'weak'

print("=== Per-factor Spearman IC vs realised_roc_annualised ===")
print(f"{'factor':>16}  {'rho':>8}  {'p-value':>10}   verdict")
print("-" * 70)
for col, _ in factors:
    if col not in df.columns:
        continue
    s = df.dropna(subset=[col, 'realised_roc_annualised'])
    if len(s) < 50:
        continue
    rho, p = spearmanr(s[col], s['realised_roc_annualised'])
    print(f"{col:>16}  {rho:+.4f}   {p:.4g}   {verdict(rho, p)}")

print("\n=== Same vs pnl_per_contract ($ basis) ===")
print(f"{'factor':>16}  {'rho':>8}  {'p-value':>10}")
print("-" * 50)
for col, _ in factors:
    if col not in df.columns:
        continue
    s = df.dropna(subset=[col, 'pnl_per_contract'])
    if len(s) < 50:
        continue
    rho, p = spearmanr(s[col], s['pnl_per_contract'])
    print(f"{col:>16}  {rho:+.4f}   {p:.4g}")

print("\n=== Mean forward ROC by quintile of each raw factor ===")
for col, label in factors:
    if col not in df.columns:
        continue
    s = df.dropna(subset=[col, 'realised_roc_annualised']).copy()
    if len(s) < 100 or s[col].nunique() < 5:
        continue
    try:
        s['q'] = pd.qcut(s[col], 5, labels=['Q1-low', 'Q2', 'Q3', 'Q4', 'Q5-high'], duplicates='drop')
    except Exception:
        continue
    g = s.groupby('q', observed=True)['realised_roc_annualised'].agg(['count', 'mean']).round(1)
    print(f"\n{col} ({label}):")
    print(g.to_string())
