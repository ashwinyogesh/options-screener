"""DITM v4 cross-sectional pipeline (ADR-0032, Phase 2b).

Wires the pure `services.scoring.ditm_v4` scorer into the existing DITM
result pipeline. The runner produces per-symbol `DitmResult` objects with
v3 scores already populated; this module then:

  1. fetches PIT fundamentals for each unique symbol
  2. builds one `Candidate` per (symbol, expiration, strike)
  3. ranks them cross-sectionally with `score_universe`
  4. writes v4 outputs back into the existing dataclass fields:

       - DitmStrikeResult.ditm_score   ← v4 percentile (overwritten)
       - DitmStrikeResult.tier         ← A/B/C/D/E
       - DitmStrikeResult.score_v4     ← v4 percentile (canonical mirror)
       - DitmStrikeResult.factor_breakdown ← signed contributions per factor
       - DitmStrikeResult.env_score    ← pillar percentile (val+cap+macro)
       - DitmStrikeResult.strike_score ← pillar percentile (tech+option)
       - DitmStrikeResult.env_detail   ← "Val:.. Cap:.. Macro:.."
       - DitmStrikeResult.strike_detail ← "Tech:.. Opt:.."
       - DitmResult.best_ditm_score    ← max v4 across strikes
       - DitmResult.best_tier          ← tier of the best strike

This is Option C of the v4 cutover: v4 is canonical (`ditm_score`/`tier`)
and the legacy `env_score`/`strike_score`/`env_detail`/`strike_detail`
fields are repurposed to carry v4-derived two-pillar percentiles for
frontend backwards-compat. The frontend will be reworked in Phase 2c.

Limitations:
  - `sector_rs_6m` is not yet sourced in production (Phase 2b minimum).
    All candidates pass None → median-imputed equally → factor is inert
    cross-sectionally (constant offset cancels out under percentile-rank).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from services import fundamentals_service
from services.scoring.ditm_v4 import (
    Candidate,
    FACTOR_DEFINITIONS,
    score_universe,
)

if TYPE_CHECKING:
    from services.ditm_service import DitmResult, DitmStrikeResult

logger = logging.getLogger(__name__)

__all__ = ["apply_v4_scoring"]


# Group membership index (factor_name → group). Built once at module load.
_FACTOR_GROUP: dict[str, str] = {name: group for name, _, group, _ in FACTOR_DEFINITIONS}
_FACTOR_WEIGHT: dict[str, float] = {name: w for name, _, _, w in FACTOR_DEFINITIONS}


@dataclass
class _StrikeRef:
    """Cursor to a strike within the result list, used for write-back."""
    result_idx: int
    strike_idx: int


def _build_factors(
    fund: dict[str, float | None],
    res: "DitmResult",
    strike: "DitmStrikeResult",
) -> dict[str, float | None]:
    """Assemble the 13-factor dict for one (result, strike) pair.

    Sourcing:
      val/cap   ← `fund` (PIT fundamentals from EDGAR)
      tech      ← `res` (per-symbol indicators)
      macro     ← None for now (sector_rs_6m wiring deferred)
      option    ← `strike` (per-strike option mechanics)

    Units don't matter for the rank-based scorer as long as they're
    consistent across all candidates in this scoring pass, which they
    are because the same code path produces every candidate.
    """
    # Option: leverage = delta * spot / mid_price (DITM-specific lever ratio)
    if strike.mid > 0:
        leverage = strike.delta * res.price / strike.mid
    else:
        leverage = None

    return {
        # Valuation (lower better — sign handled inside scorer)
        "ps_ttm":         fund.get("ps_ttm"),
        "ev_sales":       fund.get("ev_sales"),
        "ev_ebitda":      fund.get("ev_ebitda"),
        # Capital structure (higher better for DITM)
        "debt_to_equity": fund.get("debt_to_equity"),
        "nd_ebitda":      fund.get("nd_ebitda"),
        # Technicals (per-symbol)
        "wk_rsi":         res.weekly_rsi if res.weekly_rsi else None,
        "dist52w":        res.dist_from_52w_high_pct,
        "hv30":           res.hv30 if res.hv30 else None,
        "ret_200d":       res.ret_200d,
        # Macro — deferred (Phase 2b ships without sector_rs_6m)
        "sector_rs_6m":   None,
        # Option mechanics (per-strike)
        "leverage":       leverage,
        "delta":          strike.delta,
        "extrinsic_pct":  strike.extrinsic_pct,
    }


def _compute_factor_breakdown(
    factors: dict[str, float | None],
    factor_ranks: dict[str, float],
) -> dict[str, float]:
    """Per-factor signed contribution = weight * rank (or weight * 0.5 if missing).

    Returned values are the raw signed terms summed by `score_universe`. They
    are useful for downstream rendering and diagnostics; their sum is the
    candidate's `raw_weighted` (pre-percentile score).
    """
    breakdown: dict[str, float] = {}
    for fname, weight in _FACTOR_WEIGHT.items():
        rank = factor_ranks.get(fname)
        if rank is None:
            breakdown[fname] = round(weight * 0.5, 6)
        else:
            breakdown[fname] = round(weight * rank, 6)
    return breakdown


def _percentile_within(values: list[float]) -> list[float]:
    """Average-rank percentile mapping → [0, 100]. Pure helper for pillars."""
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda iv: iv[1])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        pct = avg_rank / n * 100
        for k in range(i, j + 1):
            out[indexed[k][0]] = round(pct, 2)
        i = j + 1
    return out


def _format_pillar_detail(group_contribs: dict[str, float], pillar_groups: tuple[str, ...]) -> str:
    """Render 'Val:X.X Cap:Y.Y' style detail string from group contributions."""
    label_map = {"valuation": "Val", "capital": "Cap", "technical": "Tech",
                 "macro": "Macro", "option": "Opt"}
    parts = []
    for g in pillar_groups:
        v = group_contribs.get(g, 0.0)
        # Express each group contribution as a percentage of the strategy budget,
        # so the pillar detail shows what each group contributed in 0..100 terms.
        # Group cap is the max possible |contribution| from that group; show the
        # actual value scaled into [0, 100] using cap as denominator.
        from services.scoring.ditm_v4 import GROUP_WEIGHT_CAPS
        cap = GROUP_WEIGHT_CAPS[g]
        pct = (v / cap * 100) if cap > 0 else 0.0
        parts.append(f"{label_map.get(g, g)}:{pct:+.0f}")
    return " ".join(parts)


def apply_v4_scoring(
    results: list["DitmResult"],
    asof: date | None = None,
) -> list["DitmResult"]:
    """Rank all strikes across all results cross-sectionally with the v4 model.

    Mutates the input results in-place AND returns them (for chaining).
    Safe to call with empty list. If fewer than 2 candidates are produced,
    the v4 score collapses (one row gets 100, rest get 0); that's a known
    limitation of cross-sectional scoring in tiny universes — POST /ditm
    callers will see ranks within the submitted batch only.

    `asof` defaults to today; pass an explicit date for deterministic
    backtest replay.
    """
    if not results:
        return results
    if asof is None:
        asof = date.today()

    # ------------------------------------------------------------------
    # 1. Per-symbol fundamentals (one fetch per unique ticker per scan).
    # ------------------------------------------------------------------
    unique_symbols = {r.symbol for r in results}
    fundamentals: dict[str, dict[str, float | None]] = {}
    for sym in unique_symbols:
        spot = next((r.price for r in results if r.symbol == sym), None)
        try:
            fundamentals[sym] = fundamentals_service.get_pit_factors(
                sym, asof, spot_price=spot,
            )
        except Exception as exc:
            # Defensive: a single ticker's fundamentals failure must not
            # abort the entire universe scoring pass.
            logger.warning("v4 fundamentals fetch failed for %s: %s", sym, exc)
            fundamentals[sym] = {}

    # ------------------------------------------------------------------
    # 2. Build cross-sectional candidates with stable cursors back.
    # ------------------------------------------------------------------
    candidates: list[Candidate] = []
    refs: list[_StrikeRef] = []
    factor_payloads: list[dict[str, float | None]] = []

    for ri, res in enumerate(results):
        fund = fundamentals.get(res.symbol, {})
        for si, strike in enumerate(res.strikes):
            factors = _build_factors(fund, res, strike)
            cid = f"{ri}:{si}"
            candidates.append(Candidate(id=cid, factors=factors))
            refs.append(_StrikeRef(result_idx=ri, strike_idx=si))
            factor_payloads.append(factors)

    if not candidates:
        return results

    outputs = score_universe(candidates)

    # ------------------------------------------------------------------
    # 3. Recompute per-candidate factor ranks once more (for breakdown
    #    rendering — score_universe doesn't return them). This is the
    #    same percentile-rank logic, replicated so we can publish the
    #    contribution table per strike. Cheap for typical N (<5000).
    # ------------------------------------------------------------------
    factor_names = [f[0] for f in FACTOR_DEFINITIONS]
    per_factor_ranks: dict[str, list[float | None]] = {}
    for fname in factor_names:
        vals: list[float | None] = []
        for fp in factor_payloads:
            v = fp.get(fname)
            if v is None:
                vals.append(None)
                continue
            try:
                fv = float(v)
                if fv != fv:
                    vals.append(None)
                else:
                    vals.append(fv)
            except (TypeError, ValueError):
                vals.append(None)
        # Inline avg-rank
        per_factor_ranks[fname] = _avg_rank_with_nulls(vals)

    # ------------------------------------------------------------------
    # 4. Synthesize legacy two-pillar percentiles (env / strike) so the
    #    frontend keeps working in the gap before Phase 2c.
    # ------------------------------------------------------------------
    env_groups = ("valuation", "capital", "macro")
    strike_groups = ("technical", "option")

    env_pillar_raw: list[float] = []
    strike_pillar_raw: list[float] = []
    breakdowns: list[dict[str, float]] = []
    group_contribs_per_cand: list[dict[str, float]] = []

    for i in range(len(candidates)):
        per_factor_rank: dict[str, float] = {}
        for fname in factor_names:
            r = per_factor_ranks[fname][i]
            per_factor_rank[fname] = r if r is not None else 0.5

        breakdown: dict[str, float] = {
            fname: round(_FACTOR_WEIGHT[fname] * per_factor_rank[fname], 6)
            for fname in factor_names
        }
        breakdowns.append(breakdown)

        # Sum contributions per group (signed, since weights carry sign).
        group_sum: dict[str, float] = {}
        for fname, contrib in breakdown.items():
            g = _FACTOR_GROUP[fname]
            group_sum[g] = group_sum.get(g, 0.0) + contrib
        group_contribs_per_cand.append(group_sum)

        env_pillar_raw.append(sum(group_sum.get(g, 0.0) for g in env_groups))
        strike_pillar_raw.append(sum(group_sum.get(g, 0.0) for g in strike_groups))

    env_pct = _percentile_within(env_pillar_raw)
    strike_pct = _percentile_within(strike_pillar_raw)

    # ------------------------------------------------------------------
    # 5. Write v4 outputs back into the result/strike objects in-place.
    # ------------------------------------------------------------------
    for i, out in enumerate(outputs):
        ref = refs[i]
        res = results[ref.result_idx]
        strike = res.strikes[ref.strike_idx]

        v4 = out.score
        # If a strike was ineligible (too few observed factors), preserve
        # the prior v3 ditm_score on that row but flag tier=None and
        # score_v4=None. This avoids zeroing out otherwise valid strikes
        # in tiny POST batches where some symbols may have sparse fund data.
        if v4 is None:
            strike.tier = None
            strike.score_v4 = None
            strike.factor_breakdown = breakdowns[i]
            strike.env_score = round(env_pct[i], 2)
            strike.strike_score = round(strike_pct[i], 2)
            strike.env_detail = _format_pillar_detail(group_contribs_per_cand[i], env_groups)
            strike.strike_detail = _format_pillar_detail(group_contribs_per_cand[i], strike_groups)
            continue

        strike.ditm_score = round(v4, 2)
        strike.score_v4 = round(v4, 2)
        strike.tier = out.tier
        strike.factor_breakdown = breakdowns[i]
        strike.env_score = round(env_pct[i], 2)
        strike.strike_score = round(strike_pct[i], 2)
        strike.env_detail = _format_pillar_detail(group_contribs_per_cand[i], env_groups)
        strike.strike_detail = _format_pillar_detail(group_contribs_per_cand[i], strike_groups)

    # ------------------------------------------------------------------
    # 6. Recompute per-result best_ditm_score and best_tier.
    # ------------------------------------------------------------------
    for res in results:
        if not res.strikes:
            continue
        # The strike with the highest v4 score is best; preserve is_best
        # only if it was set by the runner — if the order changes under
        # v4, recompute the flag from the new score.
        best_idx = max(range(len(res.strikes)), key=lambda k: res.strikes[k].ditm_score)
        best_strike = res.strikes[best_idx]
        res.best_ditm_score = round(best_strike.ditm_score, 2)
        res.best_tier = best_strike.tier
        for s_idx, s in enumerate(res.strikes):
            s.is_best = (s_idx == best_idx)

    return results


def _avg_rank_with_nulls(values: list[float | None]) -> list[float | None]:
    """Average-rank → fraction in (0, 1]; None positions stay None.

    Duplicated locally (not imported from `ditm_v4`) because that module's
    helper is private. Behaviour is identical and tested independently.
    """
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    n = len(indexed)
    if n == 0:
        return [None] * len(values)
    indexed.sort(key=lambda iv: iv[1])  # type: ignore[arg-type, return-value]
    out: list[float | None] = [None] * len(values)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        pct = avg_rank / n
        for k in range(i, j + 1):
            out[indexed[k][0]] = pct
        i = j + 1
    return out
