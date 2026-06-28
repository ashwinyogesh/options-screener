"""Print a side-by-side Lasso vs Method D decile comparison table."""
import pandas as pd

lasso  = pd.read_csv("csp_lasso_decile_results.csv")
methodd_data = [
    # From backtest output captured during analysis run
    ("D10", 1801, 29.8, 32.3, 85.6, 14.4,  365),
    ("D09", 1791, 17.5, 21.6, 81.1, 18.9,  214),
    ("D08", 1784, 16.6, 18.5, 80.3, 19.7,  186),
    ("D07", 1830, 12.7, 17.7, 79.8, 20.2,   91),
    ("D06", 1802,  4.2, 14.9, 78.3, 21.7,   -4),
    ("D05", 1772, -3.7, 15.7, 73.6, 26.4,  -92),
    ("D04", 1806, -5.7, 12.9, 73.2, 26.8, -132),
    ("D03", 1816, -2.0, 12.0, 76.7, 23.3,  -69),
    ("D02", 1812, -2.9,  8.6, 74.6, 25.4,  -83),
    ("D01", 1802, -4.1,  7.2, 73.5, 26.5, -141),
]
md = pd.DataFrame(methodd_data,
    columns=["Decile", "N", "MD_ROC", "MD_Med", "MD_WR", "MD_Asgn", "MD_PnL"])

SEP = "=" * 122
print()
print(SEP)
print("  LASSO vs METHOD D — DECILE COMPARISON    (n=18,016 trades · 35 DTE · 154 tickers · 2024-01→2026-04)")
print()
print("  ┌─ Overall IC (Spearman ρ of score vs realised annualised ROC) ─────────────────────┐")
print("  │  Lasso score    : ρ = 0.616   Period IC μ = 0.628  IC/IR = 2.89                  │")
print("  │  Method D score : ρ = 0.491   Period IC μ = 0.466  IC/IR = 2.99                  │")
print("  │  Lift            : +0.125 ρ  (+25.5% relative improvement)                       │")
print("  └──────────────────────────────────────────────────────────────────────────────────────┘")
print(SEP)
print(f"  {'':4}  {'—— LASSO SCORE ———————————————————':38}  {'—— METHOD D SCORE ————————————————':38}  {'—— DELTA (Lasso − MD) ————':28}")
print(f"  {'Dec':4}  {'N':>6}  {'ROC%':>7}  {'Med%':>6}  {'WinRate':>8}  {'Asgn%':>6}  {'$PnL':>7}  "
      f"  {'ROC%':>7}  {'Med%':>6}  {'WinRate':>8}  {'Asgn%':>6}  {'$PnL':>7}  "
      f"  {'ΔROC':>6}  {'ΔWR':>6}  {'Δ$PnL':>8}")
print("  " + "-" * 118)

for (_, lr), (_, mr) in zip(lasso.iterrows(), md.iterrows()):
    dec   = lr["Decile"]
    d_roc = lr["Mean ROC %"] - mr["MD_ROC"]
    d_wr  = lr["Win Rate %"] - mr["MD_WR"]
    d_pnl = lr["Mean $ PnL"] - mr["MD_PnL"]

    # Highlight markers
    tag = " ◄ top" if dec == "D10" else (" ◄ bot" if dec == "D01" else "")

    print(f"  {dec:4}  {lr['N Trades']:>6,}  {lr['Mean ROC %']:>+7.1f}  {lr['Median ROC %']:>6.1f}  "
          f"{lr['Win Rate %']:>7.1f}%  {lr['Assign %']:>5.1f}%  {lr['Mean $ PnL']:>+7.0f}  "
          f"  {mr['MD_ROC']:>+7.1f}  {mr['MD_Med']:>6.1f}  "
          f"{mr['MD_WR']:>7.1f}%  {mr['MD_Asgn']:>5.1f}%  {mr['MD_PnL']:>+7.0f}  "
          f"  {d_roc:>+6.1f}  {d_wr:>+6.1f}  {d_pnl:>+8.0f}{tag}")

print()
print("  Win Rate = % of trades where put expires worthless (not assigned).")
print("  $PnL     = mean P&L per 100-share contract.   Δ = Lasso minus Method D.")
print(SEP)

# Summary stats
top3_lasso  = lasso.head(3)["Mean ROC %"].mean()
top3_md     = md.head(3)["MD_ROC"].mean()
bot3_lasso  = lasso.tail(3)["Mean ROC %"].mean()
bot3_md     = md.tail(3)["MD_ROC"].mean()
spread_l    = lasso.head(1)["Mean ROC %"].values[0] - lasso.tail(1)["Mean ROC %"].values[0]
spread_md   = md.head(1)["MD_ROC"].values[0] - md.tail(1)["MD_ROC"].values[0]

print()
print("  SUMMARY")
print(f"  Top-3 decile mean ROC :  Lasso {top3_lasso:+.1f}%   vs   Method D {top3_md:+.1f}%   (Δ {top3_lasso-top3_md:+.1f}%)")
print(f"  Bot-3 decile mean ROC :  Lasso {bot3_lasso:+.1f}%   vs   Method D {bot3_md:+.1f}%   (Δ {bot3_lasso-bot3_md:+.1f}%)")
print(f"  D10-D01 ROC spread    :  Lasso {spread_l:.1f}%           Method D {spread_md:.1f}%")
print(f"  D10 win rate          :  Lasso {lasso.head(1)['Win Rate %'].values[0]:.1f}%         Method D {md.head(1)['MD_WR'].values[0]:.1f}%")
print(f"  D10 assignment rate   :  Lasso {lasso.head(1)['Assign %'].values[0]:.1f}%          Method D {md.head(1)['MD_Asgn'].values[0]:.1f}%")
print()
print("  NOTE: Lasso D10 earns +47.9% mean ROC vs Method D D10 +29.8% (+18.1pp).")
print("  Trade-off: Lasso D10 has higher assignment rate (20.8% vs 14.4%) because it")
print("  selects high-vol names — which pay more but also move more against you.")
print(SEP)
