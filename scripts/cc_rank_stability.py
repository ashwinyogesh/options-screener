r"""
CC rank-stability snapshot tool -- mirror of `csp_rank_stability.py` for the
covered-call screener. Validates: Spearman(score, score_at_other_time) >= 0.85
across intraday snapshots.

Two modes
---------

CAPTURE (one-shot)
    Runs the live CC screener (`services.cc_service.process_cc_symbol`) over
    the configured universe and writes one snapshot file
    ``rank_snapshot_cc_<HHMM>.csv`` with ``ticker, score, rank, premium``.

COMPARE (post-hoc)
    Loads two or more snapshot CSVs and reports pairwise Spearman rank
    correlations. Pass if all pairs >= 0.85.

LOOP (live, blocks)
    Schedules N captures at fixed minute intervals, then auto-compares.

Notes
-----
- Requires the network: this hits yfinance via the production data + options
  services. Don't run inside the test suite.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout (Windows cp1252 otherwise crashes on Unicode in scoring details).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Make `services.*` imports work when run as `python scripts/cc_rank_stability.py ...`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import pandas as pd  # noqa: E402

from services.cc_service import process_cc_symbol  # noqa: E402
from services.universe import MOMENTUM_UNIVERSE  # noqa: E402

logger = logging.getLogger("cc_rank_stability")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SPEARMAN_PASS = 0.85


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _scan_universe(tickers: list[str], min_dte: int, max_dte: int) -> pd.DataFrame:
    """Run process_cc_symbol per ticker, take the best strike per ticker."""
    rows: list[dict] = []
    for i, t in enumerate(tickers, 1):
        try:
            results, err = process_cc_symbol(t, min_dte=min_dte, max_dte=max_dte)
            if err is not None or not results:
                continue
            best_score = -1.0
            best_row: dict | None = None
            for r in results:
                for strike in r.strikes:
                    if strike.cc_score > best_score:
                        best_score = strike.cc_score
                        best_row = {
                            "ticker": t,
                            "score": strike.cc_score,
                            "env_score": strike.env_score,
                            "strike_score": strike.strike_score,
                            "strike": strike.strike,
                            "delta": strike.delta,
                            "premium": strike.premium,
                            "annualized_return": strike.annualized_return,
                            "dte": r.dte,
                            "expiration": r.expiration,
                            "spot": r.price,
                        }
            if best_row is not None:
                rows.append(best_row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan failed for %s: %s", t, exc)
        if i % 10 == 0:
            logger.info("  scanned %d / %d (kept %d)", i, len(tickers), len(rows))

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
        df["rank"] = df.index + 1
    return df


def capture(args: argparse.Namespace) -> None:
    tickers = _resolve_tickers(args)
    logger.info("Capturing CC rank snapshot for %d tickers", len(tickers))
    df = _scan_universe(tickers, args.min_dte, args.max_dte)
    if df.empty:
        sys.exit("No results returned. Market may be closed or universe filters too tight.")

    ts = datetime.now()
    tag = args.tag or ts.strftime("%H%M")
    out = Path(args.out_dir) / f"rank_snapshot_cc_{ts.strftime('%Y%m%d')}_{tag}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nWrote {len(df)} ranked tickers to {out}")
    print(df[["rank", "ticker", "score", "strike", "delta", "annualized_return"]].head(15).to_string(index=False))
    print(f"\n  Mean score: {df['score'].mean():.1f}    above-65: {(df['score'] >= 65).sum()} / {len(df)}")


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def _spearman(a: pd.Series, b: pd.Series) -> float:
    from scipy.stats import spearmanr  # type: ignore
    rho, _ = spearmanr(a, b)
    return float(rho)


def compare(args: argparse.Namespace) -> None:
    paths = sorted(Path(".").glob(args.pattern) if "*" in args.pattern else [Path(p) for p in args.snapshots])
    if not args.snapshots and "*" not in (args.pattern or ""):
        sys.exit("Pass at least 2 snapshot files or a --pattern glob.")
    if args.snapshots:
        paths = sorted(Path(p) for p in args.snapshots)
    if len(paths) < 2:
        sys.exit(f"Need at least 2 snapshots; got {len(paths)}.")

    snaps: dict[str, pd.DataFrame] = {}
    for p in paths:
        df = pd.read_csv(p)
        if df.empty or "ticker" not in df.columns or "score" not in df.columns:
            logger.warning("skipping malformed snapshot %s", p)
            continue
        snaps[p.stem] = df.set_index("ticker")

    if len(snaps) < 2:
        sys.exit("Need at least 2 valid snapshots.")

    print(f"\nLoaded {len(snaps)} snapshots:")
    for name, df in snaps.items():
        print(f"  {name}: {len(df)} tickers   above-65: {(df['score'] >= 65).sum()}")
    print()

    common = set.intersection(*(set(df.index) for df in snaps.values()))
    if len(common) < 5:
        sys.exit(f"Only {len(common)} tickers common to all snapshots -- can't compute stable rho.")
    print(f"Common tickers (intersection): {len(common)}\n")

    names = list(snaps.keys())
    print("=" * 78)
    print("PAIRWISE SPEARMAN RANK CORRELATION (CC score-based)")
    print("=" * 78)
    header = f"{'':>30}" + "".join(f"{n[-12:]:>14}" for n in names)
    print(header)
    breaches: list[tuple[str, str, float]] = []
    for ni in names:
        row = f"{ni[-30:]:>30}"
        for nj in names:
            if ni == nj:
                row += f"{'1.000':>14}"
                continue
            si = snaps[ni].loc[list(common), "score"]
            sj = snaps[nj].loc[list(common), "score"]
            rho = _spearman(si, sj)
            row += f"{rho:+14.3f}"
            if rho < SPEARMAN_PASS and ni < nj:
                breaches.append((ni, nj, rho))
        print(row)

    print()
    print("=" * 78)
    print("TOP-10 STABILITY (Jaccard on top-10 tickers between successive snapshots)")
    print("=" * 78)
    for i in range(len(names) - 1):
        a = set(snaps[names[i]].sort_values("score", ascending=False).head(10).index)
        b = set(snaps[names[i + 1]].sort_values("score", ascending=False).head(10).index)
        j = len(a & b) / len(a | b) if (a | b) else 0.0
        print(f"  {names[i][-30:]:>30}  ->  {names[i + 1][-30:]:>30}   Jaccard = {j:.2f}   common = {sorted(a & b)}")

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    if not breaches:
        print(f"  PASS -- all pairwise Spearman correlations >= {SPEARMAN_PASS}")
        print("          intraday CC ranks are stable; the 65-cutoff sorts on signal, not noise.")
    else:
        print(f"  FAIL -- {len(breaches)} pair(s) below the audit threshold of {SPEARMAN_PASS}:")
        for a, b, r in breaches:
            print(f"    {a}  vs  {b}:   rho = {r:+.3f}")
        print("          Production CC cutoff is partially driven by intra-day noise.")


# ---------------------------------------------------------------------------
# Loop (capture N times, then compare)
# ---------------------------------------------------------------------------

def loop(args: argparse.Namespace) -> None:
    tickers = _resolve_tickers(args)
    logger.info("Loop mode: %d captures, %d-min interval, %d tickers each",
                args.captures, args.interval_min, len(tickers))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i in range(args.captures):
        ts = datetime.now()
        tag = f"capture{i + 1}_{ts.strftime('%H%M')}"
        df = _scan_universe(tickers, args.min_dte, args.max_dte)
        if df.empty:
            logger.error("Empty result at %s; skipping", tag)
        else:
            out = out_dir / f"rank_snapshot_cc_{ts.strftime('%Y%m%d')}_{tag}.csv"
            df.to_csv(out, index=False)
            paths.append(out)
            logger.info("Wrote snapshot %d -> %s (%d tickers)", i + 1, out, len(df))

        if i < args.captures - 1:
            logger.info("Sleeping %d min before next capture...", args.interval_min)
            time.sleep(args.interval_min * 60)

    if len(paths) >= 2:
        args.snapshots = [str(p) for p in paths]
        args.pattern = ""
        compare(args)
    else:
        logger.error("Only %d snapshot(s) captured -- nothing to compare.", len(paths))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = list(MOMENTUM_UNIVERSE)
    if args.limit:
        tickers = tickers[: args.limit]
    return tickers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tickers", type=str, default=None,
                        help="Comma-separated tickers (default: full MOMENTUM_UNIVERSE)")
    common.add_argument("--limit", type=int, default=None,
                        help="Take the first N tickers from the universe")
    common.add_argument("--min-dte", type=int, default=30)
    common.add_argument("--max-dte", type=int, default=60)
    common.add_argument("--out-dir", type=str, default=".")

    cap = sub.add_parser("capture", parents=[common], help="One-shot capture")
    cap.add_argument("--tag", type=str, default=None,
                     help="Label appended to filename (default: HHMM)")
    cap.set_defaults(func=capture)

    cmp = sub.add_parser("compare", parents=[common], help="Compare existing snapshots")
    cmp.add_argument("snapshots", nargs="*", help="Snapshot CSVs to compare")
    cmp.add_argument("--pattern", type=str, default="",
                     help="Glob pattern instead of explicit list (e.g. 'rank_snapshot_cc_*.csv')")
    cmp.set_defaults(func=compare)

    lp = sub.add_parser("loop", parents=[common], help="Capture N times then auto-compare")
    lp.add_argument("--captures", type=int, default=4,
                    help="Number of snapshots to capture")
    lp.add_argument("--interval-min", type=int, default=90,
                    help="Minutes between captures")
    lp.set_defaults(func=loop)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
