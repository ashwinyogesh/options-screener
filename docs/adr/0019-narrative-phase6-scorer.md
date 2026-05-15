# ADR-0019: Narrative Phase 6 — ACS Scorer, Read Service, and Backtest Harness

- **Status**: Accepted
- **Date**: 2026-05-14

## Context

Phase 5 delivered `job-narrative-detector` and the missing aggregator inputs
(`tier_pct` and `contributor_count_growth_7d`) so that every `ticker_timeline`
document now carries the full lifecycle — `lifecycle_stage` (1–6),
`stage_confidence` — alongside the Phase 3 attention metrics and Phase 4
conviction ratios.

Phase 6 must turn those building blocks into a single, surfaceable score and
expose it to the existing screener UI, satisfying the three acceptance criteria
in [NARRATIVE_METHODOLOGY.md §Phase 6](../NARRATIVE_METHODOLOGY.md):

1. Backtest IC ≥ 0.04 at T+30 on a held-out 90-day window.
2. `GET /api/narrative/tickers/top` p99 < 200 ms.
3. App Insights `acs_staleness_seconds < 900` for ≥ 99% of any 24 h window.

Three implementation choices needed to be locked in before going live.

---

## Decisions

### 1. ACS computation lives in a separate worker (`job-acs-scorer`)

Rather than computing ACS inline inside the FastAPI handler or piggy-backing on
`job-aggregator`, ACS is its own 15-minute Container Apps Job
([workers/scorer/](../../workers/scorer/)). Rationale:

- **Idempotent re-scoring is cheap** — the scorer reads every `ticker_timeline`
  doc for today and re-writes ACS fields onto the same doc. This is safe
  because the math is pure: same inputs → same outputs (see
  [workers/scorer/scorer.py](../../workers/scorer/scorer.py)).
- **Latency at read time stays low** — the FastAPI handler simply selects the
  pre-computed `acs`, `acs_components`, `acs_flags`, etc. and returns them.
  No fan-out over signals; no per-request computation. This is what makes the
  p99 < 200 ms target reachable without Redis.
- **Weights are calibration-tunable without redeploy** — `acs-component-weights`
  Key Vault secret overrides the design defaults (A_max=25, B_max=20,
  C_max=20, D_max=20, E_max=15). The scorer falls back to defaults when the
  secret is absent. The backtest harness can be re-run after a weight change
  with no infra impact.
- **Component E stays zero** — market-confirmation data (RS_14d, options ratio,
  13F deltas) is deferred to Phase 6.1. The scorer reserves the column and
  weight; the read-side `AcsComponents` already exposes it. No schema churn
  when E lights up.

### 2. Read path skips Redis (Phase 6 cost optimisation)

[ADR-0014](0014-narrative-cost-substitutions.md) provisionally allocated
Redis Basic C0 for Phase 6 (~$16/mo). We chose **not** to deploy it:

- The scorer writes ACS directly onto `ticker_timeline` documents.
- Cosmos point reads on a partition key (`ticker`) are ~10 ms; ordered
  cross-partition queries (e.g. `ORDER BY c.acs DESC OFFSET 0 LIMIT 100`)
  are 50–80 ms in the central-US region.
- p99 < 200 ms is achievable from Cosmos alone for the three FastAPI routes
  (`/tickers/{ticker}/acs`, `/tickers/top`, `/emerging`).
- Reds adds operational surface (Standard tier required for any SLA) for
  no measurable user-perceived gain at current QPS.

If sustained QPS or growth in `acs_components` payload size pushes p99 past
budget, Redis can be re-introduced in Phase 6.1 with no contract change —
the read service already encapsulates Cosmos access in
[backend/services/narrative/cosmos_client.py](../../backend/services/narrative/cosmos_client.py).

### 3. Backtest is a CLI script, not a Container Apps Job

[scripts/backtest_narrative.py](../../scripts/backtest_narrative.py) is a
local-runnable harness. Decision:

- The IC target is a one-time calibration gate, not a continuous workload.
  Running it on cron would consume Container Apps minutes and OpenAI tokens
  (when the script reuses the scorer's `compute_acs` for docs missing `acs`)
  with no decision being made from the output.
- Two input sources: `--input <jsonl>` (offline, CI-friendly) and `--cosmos`
  (live, requires `COSMOS_ENDPOINT` + managed identity).
- Output is a Spearman IC, a top–bottom quintile spread, and a pass/fail
  against `--ic-threshold` (default 0.04). Per-pair ledger optionally written
  to `--out <csv>` for downstream weight calibration.
- yfinance is the price source — already in `backend/requirements.txt`
  ([ADR-0014](0014-narrative-cost-substitutions.md), polygon substitution).

---

## Consequences

**Positive**

- Three concerns (compute, read, validate) cleanly separated: each can be
  changed without touching the others.
- Weight calibration becomes a Key Vault secret update plus a re-run of
  `backtest_narrative.py` — no redeploy.
- The frontend wire format never changes when component E lights up later;
  the field already exists and renders as `0.0`.
- Tests: 37 scorer-math unit tests, 19 read-service unit tests, 10
  backtest-math unit tests — all green ([workers/scorer/tests](../../workers/scorer/tests),
  [backend/tests/unit/test_narrative_read_service.py](../../backend/tests/unit/test_narrative_read_service.py),
  [backend/tests/unit/test_backtest_narrative.py](../../backend/tests/unit/test_backtest_narrative.py)).

**Negative / accepted risk**

- Without Redis, a sudden 10× QPS spike on `/api/narrative/*` could push p99
  past 200 ms. Mitigation: monitor App Insights `request_duration_p99` for
  these routes; reintroduce Redis at first sign of breach (Phase 6.1).
- Backtest IC depends on yfinance — the same freshness/gap caveat from
  ADR-0014 applies. If a gap-day prevents a `(ticker, bucket_date)` pair from
  building, it is dropped (not interpolated) to avoid leaking future
  information.
- Component E being 0 means the current ACS is a 4-component score; the
  100-point ceiling is reached only with E_max = 15 allocated elsewhere via
  Key Vault overrides if desired. Default weights leave 15 points unallocated.

---

## Validation

- `pytest workers/scorer/tests backend/tests/unit/test_narrative_read_service.py backend/tests/unit/test_backtest_narrative.py` — 66 passed.
- `npm run build` — clean.
- `python scripts/backtest_narrative.py --input backend/tests/fixtures/narrative/timeline_sample.jsonl --horizon 30` — runs end-to-end against yfinance with a synthetic 12-pair fixture (FAIL is expected from toy data; criterion will be re-checked against the live 90-day window once Cosmos history fills out).

## References

- [NARRATIVE_METHODOLOGY.md §5](../NARRATIVE_METHODOLOGY.md) — ACS formula
- [ADR-0013](0013-narrative-intelligence-platform.md) — platform skeleton
- [ADR-0014](0014-narrative-cost-substitutions.md) — Redis deferral rationale
- [ADR-0017](0017-narrative-phase5-detector.md) — predecessor (lifecycle detector)
