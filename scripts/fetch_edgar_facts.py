"""Fetch SEC EDGAR companyfacts JSON for every ticker in MOMENTUM_UNIVERSE.

Outputs:
  data/edgar/cik_map.json          — ticker → 10-digit CIK
  data/edgar/{TICKER}.json         — raw companyfacts payload (one per ticker)

SEC fair-use rules:
  - Max 10 req/sec (we sleep 0.15s = ~6.7 rps to be safe)
  - User-Agent must identify caller; set EDGAR_UA env var or it falls back to
    'Options-Screener research@example.com' (replace if running publicly).

Run once. ~3-5 minutes for 158 tickers.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services.universe import MOMENTUM_UNIVERSE  # noqa: E402

OUT_DIR = ROOT / "data" / "edgar"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CIK_MAP_PATH = OUT_DIR / "cik_map.json"

UA = os.environ.get("EDGAR_UA", "Options-Screener research@example.com")
HEADERS = {"User-Agent": UA, "Accept": "application/json"}
RATE_SLEEP = 0.15  # ~6.7 req/sec


def fetch_cik_map() -> dict[str, str]:
    """Download SEC's master ticker→CIK file. Returns {TICKER: '0001234567'}."""
    url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    raw = r.json()
    out = {}
    for entry in raw.values():
        out[entry["ticker"].upper()] = f"{entry['cik_str']:010d}"
    return out


def fetch_companyfacts(cik10: str) -> dict | None:
    """Fetch full companyfacts JSON for a CIK. None on 404."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == 2:
                print(f"    ! giving up: {exc}")
                return None
            time.sleep(1 + attempt)
    return None


def main() -> None:
    print(f"User-Agent: {UA}")
    print("Fetching ticker→CIK map...")
    cik_map = fetch_cik_map()
    CIK_MAP_PATH.write_text(json.dumps(cik_map))
    print(f"  {len(cik_map)} tickers in master map")

    universe = list(MOMENTUM_UNIVERSE)
    print(f"\nFetching companyfacts for {len(universe)} tickers...")
    t0 = time.time()
    n_ok = n_skip = n_fail = 0
    for i, tkr in enumerate(universe, 1):
        out_path = OUT_DIR / f"{tkr}.json"
        if out_path.exists() and out_path.stat().st_size > 1000:
            n_skip += 1
            continue
        cik = cik_map.get(tkr.upper())
        if not cik:
            print(f"  [{i}/{len(universe)}] {tkr}: no CIK")
            n_fail += 1
            continue
        data = fetch_companyfacts(cik)
        if data is None:
            print(f"  [{i}/{len(universe)}] {tkr}: not found")
            n_fail += 1
        else:
            out_path.write_text(json.dumps(data))
            n_ok += 1
        if i % 20 == 0 or i == len(universe):
            print(f"  [{i}/{len(universe)}] elapsed {time.time() - t0:.0f}s   ok={n_ok} skip={n_skip} fail={n_fail}")
        time.sleep(RATE_SLEEP)
    print(f"\nDone. ok={n_ok}  cached={n_skip}  fail={n_fail}  elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
