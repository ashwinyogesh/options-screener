"""Backtest the Attention-Conviction Score (ACS) against forward returns.

Phase 6 acceptance criterion per ``docs/NARRATIVE_METHODOLOGY.md``:

    Backtest IC >= 0.04 at T+30 on held-out 90 days.

IC = Spearman rank correlation between ``acs`` at time T and the realised
forward return ``close(T + horizon) / close(T) - 1``.

Sources
-------
- ``--input <path>``   JSONL file with one ticker_timeline doc per line.
                      Useful for local runs / CI without Cosmos creds.
- ``--cosmos``        Live read from Cosmos ``ticker_timeline`` via
                      DefaultAzureCredential (requires
                      ``COSMOS_ENDPOINT`` env var).

Each doc must carry at least ``ticker``, ``bucket_date``, and ``acs``.
Docs missing ``acs`` are scored on the fly using ``workers/scorer/scorer.py``
(falls back to design weights from ``kv_secrets._DEFAULT_WEIGHTS``).

Prices come from yfinance and are cached for the duration of the run.

Usage
-----
    cd backend
    .\\venv\\Scripts\\python.exe ..\\scripts\\backtest_narrative.py \\
        --input ..\\backend\\tests\\fixtures\\narrative\\timeline_sample.jsonl \\
        --horizon 30

    .\\venv\\Scripts\\python.exe ..\\scripts\\backtest_narrative.py \\
        --cosmos --since 2026-02-01 --until 2026-04-30 --horizon 30
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import spearmanr

# Make the scorer worker importable so we can re-score docs lacking ``acs``.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "workers" / "scorer"))

from scorer import compute_acs  # noqa: E402

_DEFAULT_WEIGHTS: dict[str, float] = {
    "A_max": 25.0,
    "B_max": 20.0,
    "C_max": 20.0,
    "D_max": 20.0,
    "E_max": 15.0,
}

logger = logging.getLogger("backtest_narrative")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    logger.info("Loaded %d docs from %s", len(rows), path)
    return rows


def load_cosmos(since: str, until: str) -> list[dict]:
    """Read ticker_timeline docs with bucket_date in [since, until]."""
    import os
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.getenv("COSMOS_ENDPOINT")
    if not endpoint:
        raise RuntimeError("COSMOS_ENDPOINT must be set for --cosmos")
    db_name = os.getenv("COSMOS_DB", "narrative")
    client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    container = client.get_database_client(db_name).get_container_client("ticker_timeline")
    query = (
        "SELECT * FROM c "
        "WHERE c.bucket_date >= @since AND c.bucket_date <= @until"
    )
    docs = list(
        container.query_items(
            query=query,
            parameters=[
                {"name": "@since", "value": since},
                {"name": "@until", "value": until},
            ],
            enable_cross_partition_query=True,
        )
    )
    logger.info("Loaded %d docs from Cosmos (%s..%s)", len(docs), since, until)
    return docs


# ---------------------------------------------------------------------------
# Pair construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestPair:
    ticker: str
    bucket_date: str       # T  (ISO date)
    acs: float             # predicted at T
    forward_return: float  # realised T -> T+horizon


def ensure_acs(doc: dict) -> float | None:
    """Return ACS for a doc, computing on the fly if absent."""
    if "acs" in doc and doc["acs"] is not None:
        return float(doc["acs"])
    try:
        return compute_acs(doc, _DEFAULT_WEIGHTS).acs
    except Exception as exc:  # pragma: no cover — surfaced in summary
        logger.warning("Skip %s/%s: cannot score (%s)",
                       doc.get("ticker"), doc.get("bucket_date"), exc)
        return None


def fetch_price_panel(tickers: Iterable[str], start: str, end: str) -> pd.DataFrame:
    """Download daily Close prices for the union of tickers, deduped & cached."""
    unique = sorted(set(tickers))
    if not unique:
        return pd.DataFrame()
    logger.info("Fetching %d tickers from yfinance: %s..%s", len(unique), start, end)
    data = yf.download(
        unique,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    if isinstance(data.columns, pd.MultiIndex):
        # group_by="ticker" → columns like (TICKER, "Close")
        closes = pd.DataFrame({t: data[t]["Close"] for t in unique if t in data.columns.levels[0]})
    else:
        # Single-ticker downloads collapse to a flat frame.
        closes = pd.DataFrame({unique[0]: data["Close"]}) if "Close" in data.columns else pd.DataFrame()
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    return closes


def forward_return(closes: pd.DataFrame, ticker: str, t_date: str, horizon_days: int) -> float | None:
    """First trading day at/after t_date → first at/after t_date+horizon."""
    if ticker not in closes.columns:
        return None
    t = pd.Timestamp(t_date)
    target = t + pd.Timedelta(days=horizon_days)
    series = closes[ticker].dropna()
    if series.empty:
        return None
    on_or_after_t = series[series.index >= t]
    on_or_after_target = series[series.index >= target]
    if on_or_after_t.empty or on_or_after_target.empty:
        return None
    p0 = float(on_or_after_t.iloc[0])
    p1 = float(on_or_after_target.iloc[0])
    if p0 <= 0:
        return None
    return p1 / p0 - 1.0


def build_pairs(docs: list[dict], horizon_days: int) -> list[BacktestPair]:
    # Group by ticker; fetch one price panel covering [min_date, max_date + horizon].
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if "ticker" in d and "bucket_date" in d:
            by_ticker[d["ticker"]].append(d)

    if not by_ticker:
        return []

    all_dates = [d["bucket_date"] for ds in by_ticker.values() for d in ds]
    start = min(all_dates)
    end_dt = pd.Timestamp(max(all_dates)) + pd.Timedelta(days=horizon_days + 5)
    end = end_dt.strftime("%Y-%m-%d")

    closes = fetch_price_panel(by_ticker.keys(), start, end)

    pairs: list[BacktestPair] = []
    for ticker, ticker_docs in by_ticker.items():
        for doc in ticker_docs:
            acs = ensure_acs(doc)
            if acs is None:
                continue
            fr = forward_return(closes, ticker, doc["bucket_date"], horizon_days)
            if fr is None:
                continue
            pairs.append(BacktestPair(
                ticker=ticker,
                bucket_date=doc["bucket_date"],
                acs=float(acs),
                forward_return=float(fr),
            ))
    return pairs


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestResult:
    n: int
    ic_spearman: float
    ic_p_value: float
    mean_return_top_quintile: float
    mean_return_bottom_quintile: float
    decile_spread: float
    passes_threshold: bool


def evaluate(pairs: list[BacktestPair], ic_threshold: float = 0.04) -> BacktestResult:
    if len(pairs) < 10:
        return BacktestResult(
            n=len(pairs),
            ic_spearman=float("nan"),
            ic_p_value=float("nan"),
            mean_return_top_quintile=float("nan"),
            mean_return_bottom_quintile=float("nan"),
            decile_spread=float("nan"),
            passes_threshold=False,
        )

    acs = np.array([p.acs for p in pairs])
    fr = np.array([p.forward_return for p in pairs])

    rho, pval = spearmanr(acs, fr)

    # Top/bottom quintile mean forward return — supports interpretation when IC is small.
    order = np.argsort(acs)
    k = max(1, len(pairs) // 5)
    bot = fr[order[:k]].mean()
    top = fr[order[-k:]].mean()

    return BacktestResult(
        n=len(pairs),
        ic_spearman=float(rho),
        ic_p_value=float(pval),
        mean_return_top_quintile=float(top),
        mean_return_bottom_quintile=float(bot),
        decile_spread=float(top - bot),
        passes_threshold=bool(rho is not None and not np.isnan(rho) and rho >= ic_threshold),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="JSONL of ticker_timeline docs")
    src.add_argument("--cosmos", action="store_true", help="Read from Cosmos ticker_timeline")
    p.add_argument("--since", type=str, default=None, help="bucket_date >= (Cosmos mode)")
    p.add_argument("--until", type=str, default=None, help="bucket_date <= (Cosmos mode)")
    p.add_argument("--horizon", type=int, default=30, help="Forward-return horizon (days)")
    p.add_argument("--ic-threshold", type=float, default=0.04, help="Min IC for pass (default 0.04)")
    p.add_argument("--out", type=Path, default=None, help="Optional CSV path for per-pair ledger")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.cosmos:
        if not (args.since and args.until):
            logger.error("--cosmos requires --since and --until")
            return 2
        docs = load_cosmos(args.since, args.until)
    else:
        docs = load_jsonl(args.input)

    pairs = build_pairs(docs, horizon_days=args.horizon)
    logger.info("Built %d (ACS, forward-return) pairs", len(pairs))

    if args.out and pairs:
        df = pd.DataFrame([p.__dict__ for p in pairs])
        df.to_csv(args.out, index=False)
        logger.info("Per-pair ledger written to %s", args.out)

    result = evaluate(pairs, ic_threshold=args.ic_threshold)
    print("=" * 60)
    print(f"Backtest — horizon T+{args.horizon}d  n={result.n}")
    print(f"  Spearman IC          : {result.ic_spearman:+.4f}  (p={result.ic_p_value:.4f})")
    print(f"  Top quintile return  : {result.mean_return_top_quintile:+.4%}")
    print(f"  Bot quintile return  : {result.mean_return_bottom_quintile:+.4%}")
    print(f"  Decile spread (T-B)  : {result.decile_spread:+.4%}")
    print(f"  Threshold (>= {args.ic_threshold:.2f}) : {'PASS' if result.passes_threshold else 'FAIL'}")
    print("=" * 60)
    return 0 if result.passes_threshold else 1


if __name__ == "__main__":
    sys.exit(main())
