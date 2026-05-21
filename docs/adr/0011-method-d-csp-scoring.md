# ADR-0011 — CSP Scoring v3.4 ("Method D"): IVP-dominant ENV, flipped 52W trend, Δ-heavy strike

- **Status:** Accepted
- **Date:** 2026-05-21
- **Supersedes (for the CSP path only):** parts of ADR-0007 (v3 lean model) and ADR-0009 (v3.1 calibration). CC scoring is unchanged.
- **Related:** ADR-0031 (CSP empirical validation, v3.3 baseline)

## Context

The v3.3 CSP scorer (released 2026-05) was validated against a synthetic-BS walk-forward backtest (ADR-0031) and shown to be monotone in realised ROC with Spearman ρ = +0.266. Subsequent work extended the backtest to a 7,085-trade 3-year sample over the full `MOMENTUM_UNIVERSE` (n=113 tickers) and ran a per-factor correlation audit against realised annualised ROC.

The audit returned two uncomfortable findings:

1. **Three CSP ENV factors had ρ(factor, realised ROC) ≤ 0** across the sample:
   - SMA Alignment: ρ ≈ −0.02
   - SMA50 Slope: ρ ≈ +0.01
   - RSI(14): ρ ≈ −0.04

   These factors were drawing weight away from the one factor that *did* work — IV Percentile — without adding incremental predictive power.

2. **The CSP 52W-distance curve was wrong-signed.** v3.3 awarded full credit to stocks within 5% of their 52W high. The backtest showed those names had:
   - Lower mean realised ROC than stocks 20–35% below their high.
   - Larger loss-given-assignment ($−1,509 vs $−955 per assignment).

   The intuition that "strong stocks near highs = safer puts" did not survive contact with the data. Mean-reversion outperformed momentum-chasing for short-DTE cash-secured puts in this sample.

## Decision

Adopt **Method D** for the CSP scorer only. CC scoring is left at v3.3 — it has not been validated for these changes and any silent regression would be hidden behind the same shared helpers.

### CSP ENV (sum 100)

| Factor             | v3.3 weight | v3.4 weight | Change |
|--------------------|------------:|------------:|--------|
| IV Percentile (IVP)|          35 |      **60** | ↑      |
| 52W Trend          |          15 |      **20** | ↑ + **flipped direction** |
| SMA Alignment      |           5 |       **0** | dropped |
| SMA50 Slope        |           5 |       **0** | dropped |
| RSI(14)            |          20 |       **0** | dropped |
| Chain Median OI    |          20 |          20 | unchanged |
| Earnings (penalty) |         −15 |         −15 | unchanged |

The flipped 52W curve is:

```
pct_below ≤ 5%      → 0   pts
5 < pct_below ≤ 30% → linear 0 → 20
pct_below > 30%     → 20  pts
```

(v3.3 was the inverse: 15 pts flat top at ≤5%, decaying to 0 at 30%.)

### CSP Strike (sum 100)

| Factor               | v3.3 weight | v3.4 weight | Change |
|----------------------|------------:|------------:|--------|
| Δ (delta position)   |          25 |      **40** | ↑      |
| Bid-Ask Spread %     |          25 |      **15** | ↓      |
| OI / Volume          |          15 |          15 | unchanged |
| Annualized ROC       |          35 |      **30** | ↓      |

Curve shapes are unchanged — each per-strike point is rescaled by the new cap (`_score_delta_symmetric_methodd` = `_score_delta_symmetric` × 40/25, etc.). The final-blend formula `0.4 × env + 0.6 × strike` is unchanged.

`SCORING_VERSION` bumps `3.3.0 → 3.4.0`.

## Validation evidence

Source: `scripts/backtest_csp.py` — 7,085 trades, 35 DTE primary, 2023-05 → 2026-05, full `MOMENTUM_UNIVERSE`.

| Metric                                   | v3.3 | v3.4 (Method D) |
|------------------------------------------|-----:|----------------:|
| Spearman ρ(score, realised annualised ROC) | +0.229 | **+0.475** |
| Spearman ρ(score, philosophy_fit)         | +0.281 | **+0.546** |
| Mean realised ROC, 75+ score bucket       | +13.4% | **+19.1%** |
| Mean loss given assignment ($)            | −1,509 | **−955**   |

Robustness:

- **DTE sweep:** Method D ρ > v3.3 ρ at DTE = 21, 35, 45 (no overfitting to 35).
- **Sector sweep (GICS):** ρ is positive in every represented sector under Method D; under v3.3 four sectors had ρ < 0.
- **Expanded universe (158 tickers, 45 additions across Staples/Healthcare/Financials/Industrials/Energy/Materials/Real Estate/Consumer Disc):** ρ improves to +0.486 — the model generalises off the original momentum tickers.

## Consequences

### Positive

- CSP rank correlation with realised outcomes roughly doubles.
- The 65-cutoff carries +14.4% mean ROC of separation between "take" and "skip" (vs +6.9% under v3.3).
- Frontend factor list shrinks from 9 to 6 (4 ENV + Earnings penalty header + 4 Strike), making the score breakdown legible without the per-row "contribution" subtext (which is now removed).

### Negative / risks

- **CSP and CC scorers now diverge.** They previously shared `ENV_WEIGHTS` / `STRIKE_WEIGHTS` dicts; new `CSP_ENV_WEIGHTS` / `CSP_STRIKE_WEIGHTS` dicts are added in `config.py`. Future reviewers must remember that `compute_env_score(direction='csp'|'cc')` takes two different code paths.
- **Characterization fixtures under `backend/tests/fixtures/screener/csp/**` capture v3.3 outputs.** They need regeneration via `scripts/capture_screener_fixtures.py csp`. Integration tests under `pytest -m integration` will fail until refixtured. Unit tests (473 passing) cover the math correctly.
- **CC scorer is now out of date relative to CSP.** A CC backtest is required before any equivalent rebalance — not part of this change.

### Neutral

- `iv_rank`, `iv_hv_ratio`, `iv_stale`, `dte`, `price_above_sma50`, `sma50_above_sma200`, `sma_ratio`, `sma50_slope_pct`, `rsi` remain in the `compute_env_score` signature for back-compat; the CSP path simply ignores most of them. CC continues to consume them.

## Implementation

- `backend/services/scoring/config.py` — bumped `SCORING_VERSION`, added `CSP_ENV_WEIGHTS` / `CSP_STRIKE_WEIGHTS`. `ENV_WEIGHTS` / `STRIKE_WEIGHTS` still describe the CC path.
- `backend/services/scoring/env.py` — split into `_compute_env_score_csp_v34` and `_compute_env_score_cc_v33`; public `compute_env_score` dispatches by `direction`.
- `backend/services/scoring/strike.py` — added `_score_delta_symmetric_methodd`, `_score_bid_ask_methodd`, `_score_roc_methodd`; `compute_csp_strike_score` switched to them; `compute_cc_strike_score` unchanged.
- `backend/services/csp_backtest_service.py` — imports updated to Method D helpers; `STRIKE_QUANT_MAX = 70.0` (Δ 40 + ROC 30).
- `frontend/src/components/CspInput.tsx` — `SCORE_LEGEND` rebuilt to match Method D weights and curves; the per-row `score-factor-detail` subtext (which displayed point-contribution breakpoints under the factor name) was removed — the same content is still available in the expanded panel.
- `SCORING_REFERENCE.md` — banner bumped to v3.4; CSP-specific sections rewritten; CC sections left as v3.3.
- `backend/tests/unit/test_env_score.py` and `test_strike_score.py` — CSP-side expected values rescaled; SMA/SLP tests redirected to CC for shape coverage; direction-divergence tests rewritten around the new Method D semantics. All 473 unit tests pass.

## Follow-ups (not in this change)

- Regenerate `backend/tests/fixtures/screener/csp/**` via `scripts/capture_screener_fixtures.py csp` and re-enable integration tests for CSP.
- Run a similar empirical audit on the CC path; decide whether to port Method D equivalents.
- Decide whether universe expansion (proposed +45 tickers) ships under a separate PR after operators have a chance to react to the scoring change in isolation.
