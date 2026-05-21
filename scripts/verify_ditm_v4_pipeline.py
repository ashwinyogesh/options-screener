"""Parity check: full v4 pipeline IC vs standalone scorer.

Feeds the panel CSV through `apply_v4_scoring` (with fundamentals stubbed
from the same panel rows) and confirms the IC matches the standalone scorer
(~+0.07). Run from the repo root:

    backend\\venv\\Scripts\\python.exe scripts\\verify_ditm_v4_pipeline.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services import fundamentals_service as fs  # noqa: E402
from services.ditm_service import DitmResult, DitmStrikeResult  # noqa: E402
from services.scoring import ditm_v4_pipeline  # noqa: E402


def main() -> int:
    df = pd.read_csv(REPO_ROOT / "ditm_backtest_pit.csv")
    print(f"Loaded {len(df)} rows")

    results: list[DitmResult] = []
    row_idx_map: list[int] = []
    for i, row in df.iterrows():
        spot = float(row["spot"]) if pd.notna(row.get("spot")) else 100.0
        delta = float(row.get("delta") or 0.85)
        leverage = float(row.get("leverage") or 4.0)
        mid = (delta * spot / leverage) if leverage > 0 else 1.0
        ext_pct = float(row.get("extrinsic_pct") or 3.0)

        s = DitmStrikeResult(
            strike=spot * 0.85,
            delta=delta,
            mid=mid,
            extrinsic_pct=ext_pct,
            theta_annualized_pct=2.0,
            breakeven_pct=0.0,
            capital_efficiency_pct=10.0,
            bid_ask_spread_pct=1.0,
            chain_oi=200,
            env_score=0.0,
            strike_score=0.0,
            ditm_score=0.0,
        )
        r = DitmResult(
            symbol=f"{row['ticker']}_{i}",  # unique per row so fund-stub is per-row
            price=spot,
            sma_ratio=1.0,
            hv_rank=40.0,
            hv30=float(row.get("hv30") or 30.0),
            weekly_rsi=float(row.get("wk_rsi") or 50.0),
            ret_200d=float(row.get("ret_200d") or 0.0),
            dist_from_52w_high_pct=float(row.get("dist52w") or -10.0),
            earnings_date=None,
            days_to_earnings=None,
            earnings_within_dte=False,
            dte=120,
            expiration="2026-09-18",
            strikes=[s],
        )
        results.append(r)
        row_idx_map.append(i)

    # Per-row fundamentals stub.
    stub_table: dict[str, dict[str, float | None]] = {}
    for r, orig_idx in zip(results, row_idx_map):
        orig = df.iloc[orig_idx]
        stub_table[r.symbol] = {
            k: (orig.get(k) if pd.notna(orig.get(k)) else None)
            for k in ("ps_ttm", "ev_sales", "ev_ebitda", "debt_to_equity", "nd_ebitda")
        }

    original = fs.get_pit_factors
    fs.get_pit_factors = lambda t, asof, spot_price=None: stub_table.get(t, {})
    try:
        ditm_v4_pipeline.apply_v4_scoring(results, asof=date(2026, 5, 21))
    finally:
        fs.get_pit_factors = original

    df["v4_score_pipeline"] = [r.strikes[0].score_v4 for r in results]
    df["v4_tier_pipeline"] = [r.strikes[0].tier for r in results]

    scored = df.dropna(subset=["v4_score_pipeline"]).copy()
    print(f"scored: {len(scored)}/{len(df)}")

    rho_pipe, _ = spearmanr(scored["v4_score_pipeline"], scored["realised_roc_annualised"])
    rho_prod, _ = spearmanr(df["final_score"].dropna(), df.loc[df["final_score"].notna(), "realised_roc_annualised"])
    print(f"IC (wired pipeline): {rho_pipe:+.4f}")
    print(f"IC (production v3) : {rho_prod:+.4f}")
    print(f"Lift               : {rho_pipe - rho_prod:+.4f}")

    print("\nTier breakdown:")
    g = scored.groupby("v4_tier_pipeline", observed=True).agg(
        n=("v4_score_pipeline", "size"),
        median_ROC=("realised_roc_annualised", "median"),
        win=("pnl_per_contract", lambda x: (x > 0).mean() * 100),
    ).round(2)
    print(g.reindex(["A", "B", "C", "D", "E"]).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
