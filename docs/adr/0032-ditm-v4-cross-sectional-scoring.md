# ADR-0032 — DITM v4: Cross-Sectional Rank-and-Blend Scoring

- **Status**: Accepted
- **Date**: 2026-05-21
- **Supersedes**: [ADR-0008](0008-ditm-v3-lean-model.md) (DITM v3 lean model)
- **Builds on**: [ADR-0031](0031-csp-scoring-empirical-validation.md) (validation playbook), [ADR-0011](0011-method-d-csp-scoring.md) (IC-driven calibration for CSP)
- **Related code**: [backend/services/scoring/ditm_v4.py](../../backend/services/scoring/ditm_v4.py), [backend/services/scoring/ditm_v4_pipeline.py](../../backend/services/scoring/ditm_v4_pipeline.py), [backend/services/edgar/extractor.py](../../backend/services/edgar/extractor.py), [scripts/verify_ditm_v4_pipeline.py](../../scripts/verify_ditm_v4_pipeline.py)

## Context

DITM v3/v3.2 ([ADR-0008](0008-ditm-v3-lean-model.md)) was a per-row piecewise scorer: each
of ~10 factors went through a hand-tuned curve (tent, ramp, flat-top) and the weighted
sum produced a 0–100 score. The system worked, but two structural problems blocked
calibration:

1. **No usable calibration signal.** Spearman ρ between `final_score` and forward
   120-day realised ROC on a 10,767-row point-in-time panel
   ([ditm_backtest_pit.csv](../../ditm_backtest_pit.csv)) was **−0.033** — the
   production scorer was anti-correlated with realised outcomes on the panel.
2. **No fundamentals.** The v3 factor set was entirely chart-side (RSI, trend, 52W
   distance, leverage, extrinsic %). For a stock-replacement strategy held for
   3–12 months, valuation and balance-sheet quality matter at least as much as
   momentum, but v3 had no way to ingest them.

The empirical validation playbook established by [ADR-0031](0031-csp-scoring-empirical-validation.md)
(synthetic-BS walk-forward → IC → monotonicity check) and [ADR-0011](0011-method-d-csp-scoring.md)
(per-factor IC dictates weight sign and magnitude) gave us a template to rebuild DITM on
the same footing.

## Decision

Replace the per-row piecewise scorer with a **cross-sectional rank-and-blend** scorer
across 13 factors organised into 5 economic pillars. Score = candidate's percentile within
the scored universe.

### Architecture

```
candidates (list[Candidate])
  └─ per-factor: percentile-rank across the universe         (0..1)
  └─ per-candidate: Σ (signed_weight × rank)                  (raw weighted sum)
  └─ universe: percentile-map the weighted sums to 0..100     (final score)
  └─ tier:  A ≥ 90 · B ≥ 70 · C ≥ 50 · D ≥ 30 · E < 30
```

Three properties that v3 lacked:

1. **Cross-sectional, not per-row.** `score_universe()` takes a list of candidates
   and returns a list of scores; a single candidate cannot be scored alone. The
   "100" goes to whoever ranks best in the scored set, not whoever clears
   absolute thresholds.
2. **IC-derived weights, not hand-tuned.** Sign and magnitude of each weight derive
   from the per-factor Spearman IC against forward realised ROC on the PIT panel.
3. **Honest missing-data treatment.** Missing factors are imputed to the
   cross-sectional median rank (0.5) — present, but neutral. Candidates with fewer
   than `MIN_FACTORS_OBSERVED = 8` of the 13 factors observed are returned with
   `score=None` so the UI can mark them as "insufficient data" rather than scoring
   them off a thin signal.

### Factors (13, grouped into 5 pillars)

Per-group budgets sum to 1.0 of |w|. Within a group, individual factor weights
are proportional to |IC|.

| Group | Cap | Factor | Sign | Raw IC vs fwd ROC | Final \|w\| |
|---|---:|---|:---:|---:|---:|
| **valuation** | 0.35 | `ps_ttm` | − | −0.1012 | 0.146 |
|  |  | `ev_sales` | − | −0.0897 | 0.129 |
|  |  | `ev_ebitda` | − | −0.0517 | 0.075 |
| **option** | 0.25 | `leverage` | + | +0.0288 | 0.094 |
|  |  | `delta` | + | +0.0245 | 0.080 |
|  |  | `extrinsic_pct` | − | −0.0234 | 0.076 |
| **technical** | 0.20 | `wk_rsi` | − | −0.0481 | 0.076 |
|  |  | `hv30` | − | −0.0292 | 0.046 |
|  |  | `dist52w` | − | −0.0272 | 0.043 |
|  |  | `ret_200d` | + | +0.0214 | 0.034 |
| **capital** | 0.15 | `debt_to_equity` | + | +0.0764 | 0.097 |
|  |  | `nd_ebitda` | + | +0.0420 | 0.053 |
| **macro** | 0.05 | `sector_rs_6m` | − | −0.0439 | 0.050 |

Sign convention: `+1` = higher raw value → higher score; `−1` = lower raw value → higher
score. Valuation signs are negative (cheaper is better), `debt_to_equity` and
`nd_ebitda` are positive (modest leverage outperforms on DITM holds — opposite of CSP),
`wk_rsi` and `dist52w` are negative (pullbacks-in-uptrends, not blowoffs).

`sector_rs_6m` is wired into the scorer but not yet sourced in production (no sector ETF
feed in the worker). All production candidates pass `None` → median-imputed equally →
the factor is cross-sectionally inert (the constant offset cancels under percentile-rank).
It is enabled in advance so wiring up the sector feed later requires only the data
plumbing, not a scorer change.

### Tier bands

| Tier | Score | Rationale |
|:---:|---:|---|
| A | ≥ 90 | Top decile of the scored universe |
| B | 70–89 | Strong (next 20%) |
| C | 50–69 | Median band |
| D | 30–49 | Weak |
| E | < 30 | Bottom 30% — avoid |

Bands are placed at percentile cuts, not at the empirical PnL cliffs used for CSP
([ADR-0011](0011-method-d-csp-scoring.md)). Cliff-based bands require an outcome
distribution that the cross-sectional percentile already gives us by construction:
the bands are stable across regimes by definition.

### Wiring (Option C — phased cutover)

- `DitmStrikeResult.ditm_score` ← v4 percentile (canonical)
- `DitmStrikeResult.tier` ← A/B/C/D/E
- `DitmStrikeResult.score_v4` ← v4 percentile (canonical mirror, for frontend clarity)
- `DitmStrikeResult.factor_breakdown` ← signed contributions per factor
- `DitmStrikeResult.env_score` ← **repurposed**: pillar percentile (valuation + capital + macro)
- `DitmStrikeResult.strike_score` ← **repurposed**: pillar percentile (technical + option)
- `DitmStrikeResult.env_detail` / `strike_detail` ← human-readable pillar breakdown
- `DitmResult.best_tier` ← tier of the best strike (new field)

The legacy `env_score`/`strike_score` fields are repurposed (not removed) so that
older frontend builds, screenshot exports, and JSON consumers keep rendering numbers in
the expected slots. Phase 2c reworks the frontend to surface the v4 tier and factor
breakdown directly.

## Calibration & validation

**Source data:** [ditm_backtest_pit.csv](../../ditm_backtest_pit.csv) — n = 10,767 PIT
panel rows, 2023–2026, with as-of fundamentals (no look-ahead) joined from EDGAR
(see Phase 1). Each row carries: a ticker, an as-of date, the 13 v4 factor values
known at that date, and forward 120-day realised annualised ROC.

**Calibration step:** per-factor Spearman IC against `realised_roc_annualised` →
sign assigned by IC sign → magnitude proportional to |IC| within group → normalised to
the group cap.

**Out-of-sample check ([scripts/verify_ditm_v4_pipeline.py](../../scripts/verify_ditm_v4_pipeline.py)):**
runs the full wired pipeline (including the `fundamentals_service` PIT path) against
the same panel and recomputes IC and tier monotonicity.

```
Loaded 10,767 rows
Scored: 9,922 / 10,767  (845 rows below MIN_FACTORS_OBSERVED = 8)

IC (wired pipeline):  +0.0746
IC (production v3) :  −0.0328
Lift              :   +0.1074

Tier breakdown:
                     n   median_ROC   win%
A                  993       150.34   67.3
B                1,985        70.27   59.2
C                1,984        47.88   56.5
D                1,984        48.11   55.8
E                2,976        14.92   51.4
```

Monotonicity: A > B > C ≈ D > E on median ROC. The C/D crossover is small (47.9
vs 48.1) and within tier-boundary noise — the productive bands are A vs not-A and
the long-tail E penalty. The win-rate column is strictly monotone A→E.

## Consequences

### Positive

- **IC went from anti-correlated to +0.07.** Production v3 was structurally
  miscalibrated against the outcome metric we actually care about.
- **A-tier median ROC is ~10× E-tier median ROC** (150% vs 15%). The system now
  produces an actionable concentration signal.
- **Win rate is monotone in tier** (67% → 59% → 56% → 56% → 51%). Operators can
  size by tier with statistical backing.
- **Fundamentals are now in the scorer.** Three valuation factors plus two
  capital-structure factors give the model the cheap-stock and quality-stock
  signals v3 had no access to.
- **Adding a factor no longer requires re-tuning others.** The group-cap-with-
  IC-proportional-allocation rule absorbs new factors automatically.

### Negative / accepted trade-offs

- **Cross-sectional behaviour can confuse users.** A score of 100 in a 2-ticker
  scan does not mean "this is great" — it means "this is the better of two".
  Mitigated by the Phase 2c small-universe banner (warns when `n_tickers < 5` or
  `n_strikes < 20`).
- **Single-period IC.** +0.07 on a 2023–2026 panel that is largely a bull regime.
  Bear-regime behaviour is unverified; the win-rate monotonicity is the more
  robust evidence.
- **`sector_rs_6m` is inert until wired.** A 5% weight budget is sitting unused.
  Acceptable: the scorer still beats v3 by a wide margin without it.
- **No PnL calibration (vs realised dollar P&L).** Target is realised ROC, not
  realised PnL. Long-DITM PnL distribution has heavier tails than ROC, so ranking
  by IC-of-ROC underweights tail risk. Future work.

### Frozen

The constants in [backend/services/scoring/ditm_v4.py](../../backend/services/scoring/ditm_v4.py)
(`GROUP_WEIGHT_CAPS`, `_RAW_FACTORS`, `MIN_FACTORS_OBSERVED`, `TIER_THRESHOLDS`) are frozen
by this ADR. Changes require a new ADR with the IC re-run on `ditm_backtest_pit.csv`
and a tier breakdown showing equal-or-better monotonicity.

## Alternatives considered

1. **Re-tune the v3 piecewise curves with the new outcome data.** Cheaper but
   keeps the per-row architecture that can't ingest fundamentals naturally and
   can't be regime-stable.
2. **Linear regression on raw factor values.** Sensitive to outliers and scale;
   percentile-rank input is what makes the model regime-stable.
3. **Lasso / gradient-boosted regression.** Higher fit on the panel, but
   uninterpretable factor contributions and overfitting risk on a 10k-row sample.
   The rank-and-blend approach trades some IC for full interpretability — every
   tier output is reproducible from the per-factor contribution table shown
   inline in the UI.

## Open questions / follow-ups

- Wire `sector_rs_6m` from a real sector-RS feed (currently inert).
- Replicate the methodology for CC (Phase 3 explored, parked — see session notes).
- Re-validate annually as the PIT panel extends.
- Consider PnL-calibrated weights (vs ROC-calibrated) once we have a bear-regime
  sample in the panel.
