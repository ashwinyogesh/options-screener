"""Quick analysis of the 2022 bear market gates-off backtest."""
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

df = pd.read_csv("swing_backtest_2022_no_gates.csv")

print("=== 2022 BEAR MARKET — GATES-OFF ANALYSIS (SPY -19.4%) ===")
print(f"Total trades: {len(df)}  symbols: {df['symbol'].nunique()}")
print()

# Top trades by score
print("--- TOP 20 TRADES BY COMPOSITE SCORE ---")
cols = ["symbol", "entry_date", "setup", "final_score", "confidence", "r_realized", "exit_reason"]
top = df.nlargest(20, "final_score")[cols]
print(top.to_string(index=False))
print()

# Score band breakdown
print("--- SCORE BANDS: outcome distribution ---")
df["band"] = pd.cut(df["final_score"], [0, 35, 50, 65, 80, 100], labels=["<35", "35-50", "50-65", "65-80", "80+"])
g = df.groupby("band", observed=True).agg(
    n=("r_realized", "size"),
    win_rate=("exit_reason", lambda s: (s == "target").mean()),
    mean_r=("r_realized", "mean"),
    median_r=("r_realized", "median"),
    sum_r=("r_realized", "sum"),
).round(3)
print(g.to_string())
print()

# High-confidence trades detail
print("--- HIGH CONFIDENCE TRADES (final_score >= 65) ---")
hi = df[df["final_score"] >= 65].sort_values("final_score", ascending=False)
wins = (hi["exit_reason"] == "target").mean()
print(f"n={len(hi)}  win_rate={wins:.1%}  mean_R={hi['r_realized'].mean():.3f}  sum_R={hi['r_realized'].sum():.1f}")
print()
print(hi[["symbol", "entry_date", "setup", "final_score", "confidence", "r_realized", "exit_reason"]].head(30).to_string(index=False))
print()

# Monthly win rate for high-score trades
print("--- MONTHLY WIN RATE (score>=65 only) ---")
hi["month"] = pd.to_datetime(hi["entry_date"]).dt.to_period("M")
mg = hi.groupby("month").agg(
    n=("r_realized", "size"),
    win=("exit_reason", lambda s: (s == "target").mean()),
    mean_r=("r_realized", "mean"),
).round(3)
print(mg.to_string())
print()

# IC comparison
print("--- FACTOR IC IN 2022 ---")
for col in ["final_score", "raw_score", "bb_pos", "macd_hist", "setup_score", "rr_planned"]:
    s = df.dropna(subset=[col, "r_realized"])
    if len(s) < 10:
        continue
    rho, pval = spearmanr(s[col], s["r_realized"])
    print(f"  {col:<18}  rho = {rho:+.3f}   p = {pval:.4f}   n={len(s)}")
print()

# Best individual symbols in 2022
print("--- TOP 10 SYMBOLS BY SUM R (score>=50 only) ---")
hi50 = df[df["final_score"] >= 50]
sym_g = hi50.groupby("symbol").agg(
    n=("r_realized", "size"),
    sum_r=("r_realized", "sum"),
    mean_r=("r_realized", "mean"),
    win_rate=("exit_reason", lambda s: (s == "target").mean()),
).sort_values("sum_r", ascending=False).head(10).round(3)
print(sym_g.to_string())
