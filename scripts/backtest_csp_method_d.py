"""
Method D true backtest for CSP.

Monkey-patches the production env scorer + strike sub-scorers + the strike
normalisation constant in csp_backtest_service, then runs the full
walk-forward backtest across MOMENTUM_UNIVERSE. Writes the per-trade ledger to
csp_backtest_methodD.csv and prints a side-by-side comparison vs the existing
baseline ledger csp_backtest_full.csv.

Method D weight tables:
    ENV  : IVP 60 + Tr_flipped 20 + OI 20            (SMA, SLP, RSI dropped)
    STRIKE: Δ 40 + ROC 30 (+ BA/LQ absent in backtest, same as baseline)
    Final = 0.4 × env_score + 0.6 × strike_quant_score   (unchanged)
    Tr flipped: rewards distance FROM 52W high (was: closer to high)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import logging
import pandas as pd
from scipy.stats import spearmanr, pointbiserialr

import services.csp_backtest_service as bts
from services.universe import MOMENTUM_UNIVERSE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("methodD")

# ---------------------------------------------------------------------------
# Method D scorers — replace production ones via monkey-patch
# ---------------------------------------------------------------------------

def compute_env_score_methodD(
    *,
    iv_rank=None, iv_hv_ratio=None,
    price_above_sma50=False, sma50_above_sma200=False,
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    direction: str = "csp",
    dte=None, iv_stale=False,
    sma_ratio: float = 1.0,
    sma50_slope_pct: float = 0.0,
    iv_percentile: float | None = None,
) -> tuple[float, str]:
    """Env score under Method D weights.

    Sub-caps: IVP 60, Tr_flipped 20, OI 20  (SMA/SLP/RSI dropped, sum=100).
    """
    _ = (iv_rank, iv_hv_ratio, price_above_sma50, sma50_above_sma200,
         dte, iv_stale, sma_ratio, sma50_slope_pct, rsi)
    score = 0.0
    bk: dict[str, float] = {}

    # IVP — same percentile curve, rescaled 0..60
    p = 0.0
    if iv_percentile is not None and not math.isnan(iv_percentile):
        pct = iv_percentile
        if pct >= 90:
            p = 35.0
        elif pct >= 75:
            p = 25.0 + (pct - 75.0) / 15.0 * 10.0
        elif pct >= 50:
            p = 10.0 + (pct - 50.0) / 25.0 * 15.0
        elif pct >= 30:
            p = (pct - 30.0) / 20.0 * 10.0
    p = p * (60.0 / 35.0)
    score += p; bk["IVP"] = p

    # Tr_flipped (CSP): rewards distance FROM 52W high. Mirror of production:
    #   pct_below <= 5: p = 0   (near-high penalised)
    #   5 < pct_below <= 30: linear 0 -> 20
    #   pct_below > 30: clamp 20
    p = 0.0
    if not math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if pct_below <= 5:
            p = 0.0
        elif pct_below <= 30:
            p = (pct_below - 5.0) / 25.0 * 20.0
        else:
            p = 20.0
    score += p; bk["Tr"] = p

    # OI — same circuit-breaker curve, rescaled 0..20 (unchanged from production)
    p = 0.0
    if not math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(math.log10(chain_median_oi) / math.log10(5000), 1.0) * 20.0
    score += p; bk["OI"] = p

    # Method D drops SMA, SLP, RSI entirely. Record 0 for traceability.
    bk["SMA"] = 0.0; bk["SLP"] = 0.0; bk["RSI"] = 0.0

    # Earnings penalty unchanged
    earn_p = 0.0
    if earnings_within_dte:
        from services.scoring.config import EARNINGS_PENALTY
        earn_p = EARNINGS_PENALTY
        score += earn_p

    detail = " ".join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p:
        detail += f" Ear:{round(earn_p)}"
    return round(score, 1), detail


# Strike sub-scorers — same shapes, rescaled to Method D caps (Δ 40, ROC 30).
def _score_delta_methodD(delta: float, ideal: float) -> float:
    if math.isnan(delta):
        return 0.0
    offset = abs(delta - ideal)
    if offset <= 0.025:
        base = 25.0
    elif offset <= 0.075:
        base = 25.0 - (offset - 0.025) / 0.05 * 9.0
    elif offset <= 0.125:
        base = 16.0 - (offset - 0.075) / 0.05 * 7.0
    elif offset <= 0.175:
        base = 9.0 - (offset - 0.125) / 0.05 * 9.0
    else:
        base = 0.0
    return base * (40.0 / 25.0)


def _score_roc_methodD(roc: float) -> float:
    if roc >= 12:
        base = 35.0
    elif roc >= 8:
        base = 24.5 + (roc - 8) / 4.0 * 10.5
    elif roc >= 4:
        base = 14.0 + (roc - 4) / 4.0 * 10.5
    elif roc >= 2:
        base = 3.5 + (roc - 2) / 2.0 * 10.5
    elif roc >= 1:
        base = (roc - 1) / 1.0 * 3.5
    else:
        base = 0.0
    return base * (30.0 / 35.0)


# Apply patches
bts.compute_env_score = compute_env_score_methodD
bts._score_delta_symmetric = _score_delta_methodD
bts._score_roc = _score_roc_methodD
bts.STRIKE_QUANT_MAX = 40.0 + 30.0  # Δ + ROC under Method D

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

OUT_CSV = REPO_ROOT / "csp_backtest_methodD.csv"
YEARS = 3
DTE = 35

log.info("Running Method D backtest across %d tickers (years=%d, dte=%d)",
         len(MOMENTUM_UNIVERSE), YEARS, DTE)
df_d = bts.backtest_universe(MOMENTUM_UNIVERSE, years=YEARS, dte=DTE)
df_d.to_csv(OUT_CSV, index=False)
log.info("Wrote %s (%d trades)", OUT_CSV, len(df_d))

# ---------------------------------------------------------------------------
# Comparison vs baseline
# ---------------------------------------------------------------------------

def report(df: pd.DataFrame, name: str) -> dict:
    print(f"\n{'=' * 78}\n{name}   (n={len(df)})\n{'=' * 78}")
    roc = df["realised_roc_annualised"]; pnl = df["pnl_per_contract"]
    asn = df["assigned"].astype(int)
    rho_roc, _ = spearmanr(df["final_score"], roc)
    rho_pnl, _ = spearmanr(df["final_score"], pnl)
    rho_asn, _ = pointbiserialr(asn, df["final_score"])
    print(f"  Overall:  mean ROC = {roc.mean():+6.2f}%   "
          f"median ROC = {roc.median():+6.2f}%   "
          f"assign = {asn.mean()*100:5.1f}%   "
          f"win = {(pnl>0).mean()*100:5.1f}%   "
          f"mean pnl = ${pnl.mean():+7.0f}")
    print(f"  Spearman: rho(score, ROC) = {rho_roc:+.3f}   "
          f"rho(score, pnl) = {rho_pnl:+.3f}   "
          f"rho(score, assigned) = {rho_asn:+.3f}   "
          f"philosophy_fit = {(rho_roc - rho_asn):+.3f}")
    top = df[df["final_score"] >= df["final_score"].quantile(0.90)]
    bot = df[df["final_score"] <= df["final_score"].quantile(0.10)]
    print(f"  TOP 10% (n={len(top):4d}):  mean ROC = {top['realised_roc_annualised'].mean():+6.2f}%   "
          f"assign = {top['assigned'].mean()*100:5.1f}%   "
          f"win = {(top['pnl_per_contract']>0).mean()*100:5.1f}%   "
          f"mean pnl = ${top['pnl_per_contract'].mean():+7.0f}")
    print(f"  BOT 10% (n={len(bot):4d}):  mean ROC = {bot['realised_roc_annualised'].mean():+6.2f}%   "
          f"assign = {bot['assigned'].mean()*100:5.1f}%   "
          f"win = {(bot['pnl_per_contract']>0).mean()*100:5.1f}%   "
          f"mean pnl = ${bot['pnl_per_contract'].mean():+7.0f}")

    print(f"\n  Bucket monotonicity (final_score → realised ROC):")
    df = df.copy()
    df["bucket"] = pd.cut(df["final_score"], [0, 50, 65, 75, 85, 100],
                          labels=["0-50", "50-65", "65-75", "75-85", "85-100"])
    g = df.groupby("bucket", observed=True).agg(
        n=("final_score", "size"),
        mean_roc=("realised_roc_annualised", "mean"),
        median_roc=("realised_roc_annualised", "median"),
        win_rate=("pnl_per_contract", lambda s: (s > 0).mean() * 100),
        assigned=("assigned", "mean"),
        mean_pnl=("pnl_per_contract", "mean"),
    ).round(2)
    print(g.to_string())
    return {"rho_roc": rho_roc, "rho_pnl": rho_pnl, "rho_asn": rho_asn,
            "mean_roc": roc.mean(), "assign": asn.mean()}

baseline_path = REPO_ROOT / "csp_backtest_full.csv"
if baseline_path.exists():
    df_a = pd.read_csv(baseline_path)
else:
    df_a = None
    print("(no baseline CSV found at csp_backtest_full.csv — Method D results only)")

stats_d = report(df_d, "METHOD D — IVP-dominant, Tr flipped, Δ-heavy")
if df_a is not None:
    stats_a = report(df_a, "METHOD A — Current production (baseline)")

    print("\n" + "=" * 78)
    print("DELTA  (Method D − Baseline)")
    print("=" * 78)
    print(f"  Spearman rho(score, ROC):       {stats_d['rho_roc'] - stats_a['rho_roc']:+.3f}")
    print(f"  Spearman rho(score, assigned):  {stats_d['rho_asn'] - stats_a['rho_asn']:+.3f}")
    print(f"  Spearman rho(score, pnl):       {stats_d['rho_pnl'] - stats_a['rho_pnl']:+.3f}")
    print(f"  Mean realised ROC overall:      {stats_d['mean_roc'] - stats_a['mean_roc']:+.2f} pp")
    print(f"  Overall assignment rate:        {(stats_d['assign'] - stats_a['assign'])*100:+.2f} pp")
