"""Diagnostic: signal density per ticker in 72h vs 120h windows.

Usage (from repo root, with backend venv active):
    python scripts/diagnose_window.py

Reads from the Cosmos 'signals' container using DefaultAzureCredential (az login).
Prints a per-ticker table and a summary to help decide whether to widen the
narrative-detector WINDOW_HOURS from 72 to 120.

Interpretation:
  - count_72h < MIN_CLUSTER_SIZE  → stage would be 0 today at 72h
  - count_120h >= MIN_CLUSTER_SIZE → extending the window would rescue that ticker
  - median_gap                    → how many extra signals 120h adds on average
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# Allow running from repo root without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
ENDPOINT  = os.environ["NARRATIVE_COSMOS_ENDPOINT"]
DB        = os.environ.get("NARRATIVE_COSMOS_DB", "narrative")
CONTAINER = "signals"
MIN_CLUSTER_SIZE = 3   # matches narrative-detector default

WINDOWS = {
    "72h":  72,
    "120h": 120,
}

# --------------------------------------------------------------------------- #
# Cosmos helpers
# --------------------------------------------------------------------------- #
def _cutoff_ts(hours: int) -> int:
    return int((datetime.now(tz=timezone.utc) - timedelta(hours=hours)).timestamp())


def count_signals_per_ticker(container, window_hours: int) -> dict[str, int]:
    """Return {ticker: signal_count} for all tickers with embedded signals in window.

    Uses a projection query (SELECT VALUE c.ticker) to avoid GROUP BY, which
    the Cosmos Python SDK does not support cross-partition on Serverless.
    """
    cutoff = _cutoff_ts(window_hours)
    query = (
        "SELECT VALUE c.ticker FROM c "
        "WHERE c._ts >= @cutoff "
        "AND IS_DEFINED(c.embedding) AND c.embedding != null "
        "AND IS_DEFINED(c.conviction_direction)"
    )
    params = [{"name": "@cutoff", "value": cutoff}]
    tickers: list[str] = list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    counts: dict[str, int] = {}
    for t in tickers:
        counts[t] = counts.get(t, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    print(f"Connecting to {ENDPOINT} …")
    client    = CosmosClient(ENDPOINT, credential=DefaultAzureCredential())
    container = client.get_database_client(DB).get_container_client(CONTAINER)

    counts: dict[str, dict[str, int]] = {}
    for label, hours in WINDOWS.items():
        print(f"Querying {label} window …")
        per_ticker = count_signals_per_ticker(container, hours)
        for ticker, n in per_ticker.items():
            counts.setdefault(ticker, {})[label] = n

    # Build rows
    rows: list[dict] = []
    for ticker, w in counts.items():
        n72  = w.get("72h",  0)
        n120 = w.get("120h", 0)
        rows.append({
            "ticker":    ticker,
            "count_72h":  n72,
            "count_120h": n120,
            "gap":        n120 - n72,
            "stage0_72h":  n72  < MIN_CLUSTER_SIZE,
            "rescued_120h": (n72 < MIN_CLUSTER_SIZE) and (n120 >= MIN_CLUSTER_SIZE),
        })

    rows.sort(key=lambda r: r["count_72h"])

    # ----------- per-ticker table ----------- #
    header = f"{'Ticker':<8}  {'72h':>6}  {'120h':>6}  {'gap':>5}  {'Stage-0@72h':>11}  {'Rescued@120h':>12}"
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for r in rows:
        rescued_mark = "  ← rescued" if r["rescued_120h"] else ""
        stage0_mark  = "  ✗" if r["stage0_72h"] else ""
        print(
            f"{r['ticker']:<8}  {r['count_72h']:>6}  {r['count_120h']:>6}"
            f"  {r['gap']:>5}  {r['stage0_72h']!s:>11}{stage0_mark}"
            f"  {r['rescued_120h']!s:>12}{rescued_mark}"
        )

    # ----------- summary stats ----------- #
    total      = len(rows)
    stage0_72  = sum(1 for r in rows if r["stage0_72h"])
    rescued    = sum(1 for r in rows if r["rescued_120h"])
    gaps       = [r["gap"] for r in rows]
    median_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
    mean_72    = sum(r["count_72h"]  for r in rows) / max(total, 1)
    mean_120   = sum(r["count_120h"] for r in rows) / max(total, 1)

    print("=" * len(header))
    print(f"\nSummary (min_cluster_size={MIN_CLUSTER_SIZE})")
    print(f"  Tickers with signals in either window : {total}")
    print(f"  Stage-0 at 72h  (count < {MIN_CLUSTER_SIZE})           : {stage0_72} / {total}")
    print(f"  Rescued by 120h (would form cluster)  : {rescued} / {stage0_72 or 1}")
    print(f"  Mean signal count  — 72h / 120h       : {mean_72:.1f} / {mean_120:.1f}")
    print(f"  Median gap (extra signals 72h→120h)   : {median_gap}")
    print()
    if rescued == 0:
        print("Verdict: 72h window is not the bottleneck — "
              "widening to 120h rescues 0 tickers.")
    elif rescued / max(stage0_72, 1) >= 0.5:
        print("Verdict: widening to 120h rescues ≥50% of stage-0 tickers — "
              "consider changing WINDOW_HOURS=120.")
    else:
        print("Verdict: widening rescues some tickers but not most — "
              "check ingestion gaps or min_cluster_size instead.")


if __name__ == "__main__":
    main()
