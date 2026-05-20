# ADR-0007: Lean 8-factor scoring model (v3)

- **Status**: Accepted (ENV §IV/HV Ratio superseded by [ADR-0029](0029-scoring-v33-ivp-factor.md))
- **Date**: 2026-05-02

## Context

The v2 scoring model used 14 factors (7 ENV + 7 Strike). A May 2026 quant-trader
diagnostic surfaced five Major findings tracing back to a single root cause:
**too many correlated variables**.

Symptoms:

1. **44%-redundancy stack** — Δ (15), EM Buffer (20), and %OTM (9) at the configured
   `ideal_delta = -0.225` were all deterministic restatements of "this strike is at the
   gate's center". 44% of strike score derived from one underlying signal.
2. **Vol bias** — IV/HV (28), HV Rank (22), and ROC-via-IV (10 with vol-amplified yield)
   collectively gave high-IV names like NVDA/AMD a structural advantage of ~15 pts vs the
   stable basket (KO, JPM, CAT) — contradicting the "balanced premium-seller" thesis.
3. **Inert factors** — HV Rank structurally undervalued permanently-low-vol names; DTE
   Sweet Spot only nudged ±7 pts within a window the user already filtered hard.
4. **New cliff regressions** — HV Rank had a 3.67-pt jump at iv_rank=80; RSI-CSP had a
   4-pt cliff at RSI=35; CC near-high paid a 4-pt "participation prize" against thesis.
5. **Trend signal duplication** — SMA Alignment (15) and 52W (10) both answered "is this
   uptrending?". The 25 combined pts could be expressed in a single direction-aware
   factor.

The diagnostic concluded: *"Three of the five Major findings come down to 'we're measuring
the same thing twice.' That's not a calibration problem — it's a model-structure problem."*

## Options Considered

1. **Calibrate v2 in place** — fix the cliffs, retune the thresholds, accept residual
   redundancy.
   - Pros: minimal risk; no API/response shape changes.
   - Cons: addresses symptoms not root cause; redundancy continues to drive vol-bias and
     selection toward AI/semi names; future calibrations keep fighting the same battles.
   - **Rejected.**

2. **Lean 8-factor model. (Chosen.)**
   - Pros: each surviving factor measures an independent signal; the model is small
     enough that one human can hold the entire scoring rationale in mind; calibration
     becomes tractable; the audit's vol-bias and 44%-redundancy issues vanish by
     construction.
   - Cons: API response payload no longer reflects six contributing factors; some users
     may have built mental models around the v2 factor mix; the dropped factors are not
     all *inert* — some carry residual signal that a maximum-information model would
     retain.
   - **Accepted.**

3. **Two-tier model — keep v2 as "advanced", expose v3 as "default"** — let the user
   toggle factor sets.
   - Pros: preserves optionality.
   - Cons: doubles maintenance surface; `SCORING_REFERENCE.md` and frontend SCORE_LEGEND
     would need to support two parallel weight tables; lockstep doc-code rule becomes
     twice as expensive; unclear which model "owns" the displayed final score.
   - **Rejected.**

## Decision

Reduce the model from 14 factors to 8.

### v3 ENV (4 factors, 100 pts)

| Factor | Weight | Direction-aware? |
|--------|-------:|:-----------------:|
| IV/HV Ratio | 35 | no |
| Trend (52W high distance) | 25 | yes |
| RSI(14) | 20 | yes |
| Chain Median OI | 20 | no |

Plus the existing earnings penalty (−15) on top.

### v3 Strike (4 factors, 100 pts)

| Factor | Weight | Direction-aware? |
|--------|-------:|:-----------------:|
| Δ (delta position, symmetric bell) | 20 | yes (sign of ideal) |
| Bid-Ask Spread % | 30 | no |
| OI / Volume (per strike) | 15 | no |
| Annualized ROC | 35 | yes (capital basis) |

### Direction-aware divergences (CSP vs CC)

The 8 factors and the 8 weights are **identical** for CSP and CC. Only four inputs diverge:

1. **Trend curve direction** — CSP rewards proximity to the 52W high (uptrend reduces put
   assignment risk); CC peaks at 5–15% consolidation and zeroes the ≤5% near-high bucket
   (assignment risk for call writers).
2. **RSI sweet spot** — CSP 42–62; CC 38–58. Same shape, shifted left for CC.
3. **Δ ideal sign** — CSP −0.225, CC +0.225. Symmetric bell math is identical.
4. **ROC capital basis** — CSP uses `strike − credit` (cash-secured); CC uses
   `current_price − credit` (capital tied in the underlying).

Implementation: shared helpers in `strike.py` (`_score_bid_ask`, `_score_liquidity`,
`_score_roc`, `_score_delta_symmetric`) handle the strategy-agnostic math; CSP and CC
public functions call them with strategy-specific inputs.

### Dropped factors (6 total)

1. **HV Rank (22 pts in v2)** — correlated with IV/HV; structurally penalized
   permanently-low-vol names like KO/PG/JNJ that the balanced thesis should *prefer*.
2. **SMA Alignment (15 pts)** — collapsed into the new Trend factor; redundant with 52W.
3. **DTE Sweet Spot (7 pts)** — already enforced as a hard filter via user-supplied
   min/max DTE; the additional ±7 pt nudge added no decision-relevant information.
4. **EM Buffer (20 pts)** — deterministic at the configured ideal_delta. The v2 fix to use
   a 0.5×EM reference made the factor reachable but it remained ~20 pts for any strike at
   the gate's center, contributing 0 ranking signal within the gate. Still computed for
   diagnostic visibility — see "Diagnostic preservation" below.
5. **% OTM from Spot (9 pts)** — deterministic function of Δ and IV (`OTM% ≈ σ√T × 0.755`
   at Δ = -0.225). Redundant with Δ. Still computed for diagnostic visibility.
6. **S/R Distance (18 pts)** — fragile volume-profile swing-detection heuristic; high
   implementation cost (~50 LOC + a separate vol_supports/vol_resistances pipeline) for
   low signal value. The v2 weight (18) was the second-largest in Strike but the curve
   awarded full credit to a wide band that captured most in-gate strikes anyway.

### Cliff fixes (within surviving curves)

In addition to the structural change, three v2 cliffs are corrected:

1. **#2 RSI-CSP cliff at RSI=35** — v2 awarded a flat 2 pts for RSI 30–35 then jumped to
   6 at RSI=35 (4-pt cliff). v3 removes the 30–35 floor; the 35–42 ramp now starts
   continuously at 0.
2. **#5 CC ≤5% near-52W-high** — v2 paid 4 pts (40% of the factor cap) for stocks within
   5% of the 52W high, contradicting the assignment-risk thesis. v3 zeroes this bucket.
3. **#6 ROC cliff at ROC=4** — v2 jumped from 0 to 1 pt at exactly ROC=4. v3 adds a 2–4%
   ramp from 0 to 3.5 pts for continuous behavior.

The v2 HV Rank cliff at iv_rank=80 (finding #1) is moot — HV Rank is dropped entirely.

### Δ asymmetry fix (audit #7)

v2's Δ scorer awarded the aggressive wing (Δ < -0.30) 5.83 pts but the conservative wing
(-0.15 < Δ ≤ -0.10) only 5.0 pts despite equal distance from the ideal -0.225. v3 uses a
strictly symmetric bell:

```
offset = abs(delta - ideal)
≤ 0.025 → 20 pts (sweet)
≤ 0.075 → 13 pts (inner band)
≤ 0.125 →  7 pts (gate edge)
> 0.125 →  0 pts
```

Both wings score equally at any given offset.

### Diagnostic preservation

`em_buffer_pct`, `otm_pct`, and `dist_pct` continue to be computed and returned in the
strike-level response payload so frontend table columns remain populated. They contribute
**0 to the score** in v3.

This was a deliberate choice over fully removing the fields:

- The frontend tables show these as columns; removing them would force a coordinated
  frontend change in the same commit (more risk).
- They retain diagnostic value: a user inspecting a low-scoring row can still see whether
  the strike sits inside the 0.5×EM boundary, even though the model no longer scores it.

A future ADR may remove these fields entirely once the frontend is migrated to a v3-only
column set.

## Consequences

### Positive

- **Vol bias eliminated by construction.** With HV Rank dropped, the only vol-derived
  ENV factor is IV/HV. ROC's vol amplification is partially balanced by the higher Bid-Ask
  weight, which low-vol names typically pass with full credit.
- **44%-redundancy stack broken.** Δ + EM + %OTM was three votes for one signal; v3 has
  one Δ vote.
- **Stable-basket viability restored.** KO/JPM/CAT no longer pay the structural HV Rank
  penalty. Reachability evidence (in the audit) showed they now score competitively
  against AI names within their respective vol regimes.
- **Calibration tractable.** 8 factors × 4–6 thresholds each = ~40 numbers to defend,
  vs ~80 in v2. Future calibration sessions can hold the full table in working memory.
- **Cliffs eliminated.** All piecewise functions in v3 are continuous at every breakpoint.

### Negative

- **Lost-signal cost.** Each dropped factor carried *some* residual information. Most
  notably, S/R Distance was the only factor encoding price-level structure — names with
  clean post-breakout uncharted territory no longer earn the +5/+7 bonus that v2 awarded.
  Acceptable: the audit characterized S/R as fragile and the bonus only fired
  occasionally.
- **Response payload churn.** Six factor-detail strings disappear from `env_detail` and
  `strike_detail`. Existing frontend code reading these strings via regex would break.
  Mitigation: the frontend reads structured fields (`env_score`, `strike_score`,
  `csp_score`, `cc_score`, plus the diagnostic raw values), not the detail strings.
  Detail strings are display-only.
- **No A/B period.** v3 ships as a hard cutover. There is no toggle to compare v2 and v3
  output side-by-side, nor any backtest validation. The user has accepted this trade in
  favor of a single source of truth.

### Neutral

- **Final-score range unchanged.** Both halves still cap at 100; final blend is still
  `0.4 × env + 0.6 × strike`. The same tier thresholds (≥70 strong, 45–69 moderate,
  <45 weak) continue to apply but the *distribution* of scores will shift — typical
  stable-basket scores will rise; typical AI-bucket scores will fall slightly.

## Open questions / follow-up

1. **EM Buffer hard filter.** The audit recommended replacing the scored EM Buffer with a
   hard filter (`reject candidates where sigmas_outside < 0` against the 0.5×EM boundary).
   v3 does **not** implement this — `em_buffer_pct` is computed and returned but no
   candidate is rejected for being inside the boundary. Rationale: the delta gate
   (`-0.35, -0.10` for CSP) already excludes most strikes that would fail this check at
   the configured ideal_delta. Adding the hard filter touches `screener/runner.py` and
   warrants its own ADR after observing v3 output.
2. **IV Rank vs HV Rank.** Once true ATM IV history is captured in the data layer, replace
   the current HV-derived IV/HV ratio with a real IV Rank that measures vol richness vs
   the name's own historical regime. This would address the residual concern that low-vol
   names with structurally elevated IV (insurance/utilities pre-event) still under-score.
3. **Earnings penalty calibration.** The −15 ENV penalty translates to only −6 final pts
   (×0.4 blend). The audit (#12) noted this is light given the gap-risk severity.
   Considered for v3.1: either raise to −25 or implement as a hard filter.
4. **Sector / portfolio-level lens.** v3 still ranks individual rows in isolation. A
   future enhancement could add a portfolio-level constraint (e.g., max 3 rows per GICS
   sector in top-10) to prevent silent sector concentration.

## References

- Quant-trader diagnostic (May 2026): captured in conversation transcript; formal report
  archived as the `Quant Review` output that produced findings #1–13.
- [SCORING_REFERENCE.md](../../SCORING_REFERENCE.md) — v3 canonical methodology.
- [ADR-0002](0002-unified-screener-service.md) — `ScreenerConfig` pattern that the v3
  scorers continue to plug into via the existing adapters.
- [ADR-0005](0005-csp-capital-constraint.md) — capital-gate placement, including the
  prerequisite OI-aggregation ordering fix that was in scope for v2.
