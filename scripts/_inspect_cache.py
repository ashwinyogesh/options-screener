"""Inspect the structure of the fundamentals cache."""
import pickle

with open("data/fundamentals_cache.pkl", "rb") as f:
    c = pickle.load(f)

print("Universe loaded:", len(c["fundamentals"]))
print("Sector ETFs columns:", list(c["sector_etfs"].columns))
print()
for t in ["AAPL", "XOM", "JPM", "VST", "PLTR"]:
    r = c["fundamentals"].get(t)
    if not r:
        print(f"{t} missing")
        continue
    print(f"=== {t} (sector={r['sector']}) ===")
    inc = r["income_q"]
    bs = r["bs_q"]
    cf = r["cf_q"]
    print("income_q shape:", inc.shape, "cols:", [str(x.date()) for x in inc.columns][:5])
    print("income_q rows (first 15):", list(inc.index)[:15])
    print("bs_q rows (first 15):", list(bs.index)[:15])
    print("cf_q rows (first 15):", list(cf.index)[:15])
    sh = r["shares"]
    if sh is not None and len(sh) > 0:
        print("shares head:", sh.head(2).to_dict())
    print()
