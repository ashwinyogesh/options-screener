# ADR-0010: Swing market-regime engine

- **Status**: Accepted
- **Date**: 2026-05-11

## Context

The Swing screener (v1.0) ranked setups using a single static R:R hard gate (≥ 2.5)
and a flat additive composite, regardless of the prevailing market environment. A
quant-trader audit (May 2026) flagged this as the most material structural defect:

1. **Same gate in any tape.** A 2.5 R:R breakout in a VIX-12, SPY-trending-bull tape
   is a fundamentally different bet than the same setup in a VIX-30 risk-off tape with
   bear-stack indices. The model treated them identically.
2. **No top-down filter.** Reversion setups firing into a bear stack are coin flips at
   best; the screener happily ranked them and even surfaced them as "high-confidence"
   when the rest of the math aligned.
3. **No volatility-adjusted sizing signal.** Without a regime view, downstream
   guidance (entry, hold window, position sizing) had no anchor for caution.

## Options Considered

1. **Per-symbol regime via SPY beta and individual RS.**
   - Cons: re-derives the same global signal n times; expensive; results drift across
     the scan as upstream data updates between symbol calls.
   - **Rejected.**

2. **External regime feed (e.g., NDR / Jurik / 3rd-party API).**
   - Cons: introduces a paid dependency and a single point of failure for an entire
     scan; conflicts with the project's "no silent network calls in tests" rule
     because mocking becomes brittle across providers.
   - **Rejected.**

3. **Compute once per scan, in-house, from data we already fetch (chosen).**
   - SPY trend (close vs EMA21 vs EMA50) → bull / neutral / bear.
   - VIX 1y rolling percentile → calm / normal / elevated / shock.
   - Universe breadth (% > EMA50) computed from the OHLC frames we already pre-fetch
     for scoring.
   - IWM/SPY 20-day relative strength → small-cap risk appetite.
   - Composite `risk_on_score` weights: 35 / 25 / 25 / 15.
   - Labels: ≥ 65 → `risk_on`; < 40 → `risk_off`; otherwise `neutral`.
   - **Accepted.**

## Decision

Add `backend/services/swing/regime.py` exposing `compute_regime(spy_df, universe_ohlc)`
returning a `RegimeState` dataclass. The regime is computed exactly **once per scan**
in `swing_service.run_scan` after the OHLC pre-fetch stage and returned alongside
the result rows as a tuple `(rows, regime)`, which the router echoes in the
response.

> **Update (Phase-1 cleanup):** an earlier version of this ADR memoized the
> regime in a process-global `scan_cache.regime_cache["regime:global"]`. That
> single-key cache leaked one user's regime snapshot into another's request,
> could not be keyed to `as_of`, and acted as a side-channel between
> `run_scan` and its callers. It has been removed; `run_scan` now returns the
> regime explicitly.

### Outputs that flow downstream
- **`rr_gate`** — replaces the static gate. `risk_on=2.5`, `neutral=2.75`, `risk_off=3.0`.
  These values are calibrated to remain achievable given the current degenerate R:R
  shape in `risk.py` (R:R is identically the setup's R-multiple; see the TODO in
  `services/swing/risk.py`). When `risk.py` is reworked to compute technical targets,
  these gates should be raised back to ~2.5 / 3.0 / 3.5 in a follow-up ADR.
- **`multiplier`** — linear in `risk_on_score` between `REGIME_MULT_MIN=0.6` and
  `REGIME_MULT_MAX=1.0`. Multiplied into the composite in `compute_swing_score`.
- **`disable_setups`** — `["reversion"]` in `risk_off`; empty otherwise. Reversion
  longs into a confirmed bear stack are mechanically excluded.

### Failure mode
`compute_regime` never raises. If SPY/VIX/IWM fetches fail, the missing inputs fall
back to **neutral defaults** (score 50) and the returned state has `degraded=True`. The
UI surfaces this as a yellow warning in the regime banner; scoring continues with the
neutral multiplier so a transient yfinance hiccup doesn't drop a whole scan.

### Multiplier never zero
Multipliers floor at 0.6 (regime), 0.5 (earnings), 0.7 (extended). A "bad" regime
penalises but never erases a setup, preserving the ability to debug whether a low score
is driven by structural weakness or environmental headwind.

## Consequences

- ✅ R:R gate now reflects environmental risk; reversion blocked when the tape is
  hostile.
- ✅ Composite score is regime-aware without losing per-symbol granularity.
- ✅ One additional `^VIX` and one `IWM` fetch per scan (cached at the data-service layer).
- ✅ `GET /api/screener/swing/regime` exposes the cached state for UIs / external
  consumers.
- ⚠ Adds a global side-effect to the scan pipeline; tests must patch
  `services.swing.regime.get_ohlc` rather than calling the real network.
- ⚠ Threshold constants (`RISK_ON_THRESHOLD=65`, `RISK_OFF_THRESHOLD=40`, the VIX
  bands, the multiplier band) are calibration choices; future tuning requires an ADR
  amendment + matching update to `SCORING_REFERENCE.md`.

## References

- `backend/services/swing/regime.py`
- `backend/services/swing_service.py` (`run_scan`, `process_symbol`)
- `backend/routers/swing.py` (`GET /swing/regime`)
- `backend/tests/unit/test_swing_regime.py`
- `SCORING_REFERENCE.md` — Swing v2.0.0 section
- ADR-0011 (event-risk scoring), ADR-0012 (hybrid scoring)
