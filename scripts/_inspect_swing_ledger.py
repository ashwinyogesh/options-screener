import pandas as pd

df = pd.read_csv("swing_backtest_universe.csv")
print(f"Ledger: {len(df)} trades, {df['symbol'].nunique()} symbols")
print()
print("Columns:")
for c in df.columns:
    dt = str(df[c].dtype)
    nnan = df[c].isna().sum()
    if df[c].dtype == object:
        nu = df[c].nunique()
        sample = df[c].dropna().iloc[0] if df[c].notna().any() else "NA"
        print(f"  {c:32s}  {dt:10s} unique={nu:4d}  nan={nnan:5d}  e.g. {sample!r}")
    else:
        mn = df[c].min() if df[c].notna().any() else "NA"
        mx = df[c].max() if df[c].notna().any() else "NA"
        print(f"  {c:32s}  {dt:10s} nan={nnan:5d}  min={mn}  max={mx}")
