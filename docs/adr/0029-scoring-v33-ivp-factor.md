# ADR-0029: Replace IV/HV Ratio with IV Percentile in CSP/CC ENV scorer (v3.3)

- **Status**: Accepted
- **Date**: 2026-05-19
- **Supersedes**: ENV factor §IV/HV Ratio in [ADR-0007](0007-scoring-v3-lean-model.md)

## Context

The v3 lean model (ADR-0007) allocated 35 pts to IV/HV Ratio — the ratio of 30-day
implied volatility to 30-day realised volatility — as the primary "are options priced
rich?" signal. A May 2026 audit of the CSP screener surfaced two structural problems
with this choice:

1. **Noise in low-liquidity names.** `yfinance` returns implied volatility inferred from
   last-traded option prices. For thinly-traded tickers, the implied volatility series
   is stale, wide-spread, or derived from a single contract. The resulting IV/HV ratio
   is unreliable: a stale IV can read 0.2× or 3× HV depending on the last fill.

2. **HV double-counting.** HV is already embedded in the options chain pricing — market
   makers are not unaware of realised vol. Dividing IV by HV to produce the ratio
   effectively recombines two quantities that are co-determined by the same underlying
   realised-vol process. The ratio adds less independent information than it appears to.

A parallel finding: the `iv_stale` guard introduced to handle (1) was silently awarding
0 pts to all stale rows, but the test suite had not been updated to cover the IVP curve
introduced in v3.3, leaving `iv_hv_ratio`-based tests as the only ENV coverage. This was
caught during the audit as CF-1 (broken test coverage).

**IV Percentile (IVP)** — the percentile rank of today's IV within its own trailing
252-day distribution — avoids both problems:

- It compares IV only against itself, eliminating the HV dependency.
- It is well-behaved for low-liquidity names as long as the IV history is available (the
  same condition under which IV/HV would have been available).
- It has direct intuitive meaning for options sellers: "today's IV is in the Nth percentile
  of its own history — is it unusually rich or cheap?"

## Options Considered

1. **Keep IV/HV Ratio, fix the stale-IV guard more aggressively** — e.g., require ≥ 5
   liquid strikes before accepting the IV series as valid.
   - Pros: no scoring-curve change; backward compatible with existing score history.
   - Cons: does not fix the HV double-counting problem; the stale-IV boundary is arbitrary
     and will need tuning again as new tickers enter the universe; the fundamental
     correlation issue remains.
   - **Rejected.**

2. **IV Percentile (IVP) in place of IV/HV Ratio. (Chosen.)**
   - Pros: single-asset comparison eliminates HV dependency; well-defined for any ticker
     where `compute_iv_rank_percentile()` already runs (all screener tickers); clean
     piecewise-linear curve mirrors the existing factor style; independent signal from
     the remaining Trend/RSI/OI factors.
   - Cons: breaks score comparability with historic rows (any stored score from v3.2 and
     earlier reflects IV/HV, not IVP); test suite required a full rewrite of IVP-specific
     elbows.
   - **Accepted.**

3. **Combine both signals — e.g. 20 pts IVP + 15 pts IV/HV Ratio.**
   - Pros: redundancy hedge.
   - Cons: reintroduces the correlation problem ADR-0007 was designed to eliminate; total
     ENV weight stays at 100, so we would have to steal pts from another factor; 100 pts
     total is already tight.
   - **Rejected.**

## Decision

Replace the IV/HV Ratio factor (35 pts) in `compute_env_score()` with IV Percentile (IVP,
35 pts). Weight unchanged. Scoring version bumped to `3.3.0`.

### IVP piecewise-linear curve

$$
\text{IVP pts} = \begin{cases}
0 & \text{if } p < 30 \\
10 \cdot \frac{p - 30}{20} & \text{if } 30 \le p < 50 \\
10 + 15 \cdot \frac{p - 50}{25} & \text{if } 50 \le p < 75 \\
25 + 10 \cdot \frac{p - 75}{15} & \text{if } 75 \le p < 90 \\
35 & \text{if } p \ge 90
\end{cases}
$$

where $p$ = `iv_percentile` (0–100). `iv_percentile=None` → 0 pts.

### Unchanged

- `iv_hv_ratio`, `iv_stale`, `iv_rank` parameters remain in the `compute_env_score()`
  signature but are explicitly ignored (`_ = iv_rank, iv_hv_ratio, iv_stale`). This
  preserves all existing call-sites without modification.
- All other ENV factors (Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20) and the earnings
  penalty (−15) are unchanged.
- Strike scorer, final score formula, and DITM scorer are unchanged.

### Files changed

| File | Change |
|------|--------|
| `backend/services/scoring/env.py` | IVP curve replaces IV/HV curve |
| `backend/services/scoring/config.py` | `SCORING_VERSION = "3.3.0"` |
| `backend/tests/unit/test_env_score.py` | Rewritten to cover IVP elbows (31 tests) |
| `SCORING_REFERENCE.md` | ENV table + section updated to IVP |

## Consequences

- **Positive:** ENV score is now driven by five independent signals (IVP, Tr, SMA, RSI,
  OI) with no hidden HV dependency. Stale-IV edge cases no longer silently zero-out 35 pts
  in ways that are hard to explain to users. IVP is directly user-communicable ("IV is
  in the 80th percentile of its own history").
- **Negative:** Score history discontinuity. Any cached or stored scores from v3.2 and
  earlier are not comparable to v3.3 scores for the same symbol/date. Characterization
  fixtures regenerated; any external tooling (backtests, scripts using stored scores)
  must be aware of the version boundary at `SCORING_VERSION = "3.3.0"`.
- **Neutral:** `iv_hv_ratio` is still computed per-strike in `csp_service.py` and
  returned in the API response payload as a diagnostic field. Its presence in the response
  is unchanged; it simply no longer contributes to the score.

## Follow-ups

- [ ] Update frontend `SCORE_LEGEND` arrays in `CspInput.tsx` / `CcInput.tsx` to reference
  "IV Percentile" instead of "IV/HV Ratio" in any tooltip or legend copy.
- [ ] Backtest scripts that read stored scores should gate on `scoring_version` field to
  avoid mixing v3.2 and v3.3 rows.
- [ ] Consider adding `scoring_version` to the API response shape so callers can detect
  the boundary programmatically.
