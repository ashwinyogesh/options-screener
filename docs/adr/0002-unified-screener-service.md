# ADR-0002: Unified Screener Service

- **Status**: Accepted
- **Date**: 2026-04-28

## Context

The backend shipped with three near-duplicate per-symbol screeners — Cash-Secured Put (CSP), Covered Call (CC), and Deep-In-The-Money (DITM) — implemented as separate services in [backend/services/csp_service.py](../../backend/services/csp_service.py), [backend/services/cc_service.py](../../backend/services/cc_service.py), and [backend/services/ditm_service.py](../../backend/services/ditm_service.py). Together they were ~1,700 lines and roughly 95% structurally identical. Every screener executed the same pipeline:

1. Fetch the option chain for the symbol.
2. Loop over candidate expirations.
3. Build per-strike candidate rows from the chain.
4. Score the symbol environment and each strike.
5. Pick a best candidate, apply tie-breaks, assemble a result dataclass.

What actually differed between the three was small and parameterisable:

- Delta range and ideal delta for candidate selection.
- Open-interest band relative to the candidate's delta (and whether the band is `<=` or `<`).
- Strike sort direction (ascending for CC, descending for CSP and DITM).
- Tie-break key after the final blended score.
- Final blend weights between environment and strike scores (`(0.4, 0.6)` for CSP/CC, `(0.5, 0.5)` for DITM).
- Candidate filtering (DITM enforces `delta >= 0.60`).
- Result dataclass shape.

The cost of duplication was concrete, not theoretical. Bug fixes — earnings-window logic, OI median computation, IV fallback handling — had to be applied in three places by hand, and drift had already begun to creep in. Anyone touching scoring math had to grep across services and reason about whether a change applied uniformly.

Before any consolidation, Phase 0 captured nine characterization fixtures (3 tickers × 3 screeners) at [backend/tests/fixtures/screener/](../../backend/tests/fixtures/screener/) and a baseline integration test at [backend/tests/integration/test_screener_baseline.py](../../backend/tests/integration/test_screener_baseline.py). Any consolidation could then be proven bit-for-bit safe before merge.

## Options Considered

1. **Status quo — keep three duplicated services and fix bugs in lockstep manually.**
   - Pros: zero migration risk; each service stays independently readable.
   - Cons: bug-fix-once-fix-thrice tax compounds; drift inevitable; new screener variants cost ~600 lines of copy-paste each.

2. **Inheritance hierarchy — `BaseScreener` abstract class with `process_symbol`, screeners override hooks.**
   - Pros: familiar pattern; IDE navigation works out of the box.
   - Cons: shared state via `self` blurs which knobs each screener actually depends on; subclass overrides can quietly mutate base behaviour; pure-function testability is harder. Discovered hooks are a worse contract than declared ones.

3. **Composition via `ScreenerConfig` — a frozen dataclass of pure-function hooks consumed by a single runner. (Chosen.)**
   - Pros: every variant difference is a named field on one dataclass; hooks are pure functions of declared inputs and trivially testable in isolation; the runner's signature is the contract; no inheritance graph to reason about.
   - Cons: adds an indirection layer; the runner has to be careful about argument plumbing because the hooks can't reach back into it.

4. **Code generation — describe each screener as a YAML/TOML manifest and generate Python.**
   - Pros: declarative; non-Python contributors could read it.
   - Cons: overkill for three variants; debugging generated code is worse than debugging adapters; no generator framework already in the repo.

## Decision

Consolidate into a single generic runner driven by a frozen `ScreenerConfig` dataclass.

### Shape

- A single runner at [backend/services/screener/runner.py](../../backend/services/screener/runner.py) implements the chain-fetch → expiration loop → strike build → scoring → result assembly pipeline once.
- The type surface lives in [backend/services/screener/types.py](../../backend/services/screener/types.py) and [backend/services/screener/config.py](../../backend/services/screener/config.py), exporting:
  - Data contracts: `Indicators`, `SymbolMetrics`, `StrikeBuildInputs`, `StrikeContext`, `Candidate`, `StrikeBundle`, `ExpirationContext`.
  - The orchestrator contract: `ScreenerConfig` plus the callable type aliases its fields use.
- Each screener service collapses to a thin module exporting `<SCREENER>_CONFIG` and a `process_*` wrapper that delegates to `runner.run(...)`.
- Variant-specific logic lives in pure adapter functions co-located with each service:
  - `_<screener>_symbol_factory` — builds `SymbolMetrics` from raw indicators.
  - `_<screener>_strike_context_builder` — derives the `StrikeContext` (e.g. ideal delta, OI band) for a given expiration.
  - `_<screener>_env_scorer` — environment score from `SymbolMetrics`.
  - `_<screener>_strike_scorer_adapter` — strike score from a `Candidate`.
  - `_<screener>_tie_break` — lex-comparable tuple appended after `final_score`.
  - `_<screener>_result_factory` — assembles the screener-specific result dataclass.

### Config knobs that capture the variant differences

| Knob | Purpose | CSP | CC | DITM |
|------|---------|-----|-----|------|
| `delta_range` | Candidate delta window | screener-specific | screener-specific | screener-specific |
| `ideal_delta` | Target delta inside the range | screener-specific | screener-specific | screener-specific |
| `oi_delta_band` | Width of the OI peer band around the candidate's delta | screener-specific | screener-specific | screener-specific |
| `oi_delta_band_inclusive` | Whether the band uses `<=` (DITM legacy) or `<` (CSP/CC) | `False` | `False` | `True` |
| `strike_sort` | Sort direction over strikes | `"desc"` | `"asc"` | `"desc"` |
| `candidate_delta_predicate` | Extra candidate filter | none | none | `delta >= 0.60` |
| `final_blend` | `(env_weight, strike_weight)` | `(0.4, 0.6)` | `(0.4, 0.6)` | `(0.5, 0.5)` |
| `tie_break_key` | `Callable[..., tuple[float, ...]]`, lex-compared after `final_score` | screener-specific | screener-specific | screener-specific |

### Migration

Delivered in five phases, each ending green on the nine characterization fixtures with a per-phase commit:

- **Phase 0** — capture fixtures and baseline test (safety net).
- **Phase 1** — extract pure indicator and scoring modules into [backend/services/scoring/](../../backend/services/scoring/) and [backend/services/indicators.py](../../backend/services/indicators.py).
- **Phase 2** — define the type surface in `screener/types.py` and `screener/config.py`.
- **Phase 3** — build the runner and migrate CSP first (commit `d298ac8`).
- **Phase 4** — migrate CC and DITM (commit `bc726b2`).

Bit-for-bit parity was preserved on all nine characterization fixtures across the migration. Legacy bodies are retained in-file as `_legacy_process_*_symbol` to keep one-commit revert available; they are scheduled for removal in a later cleanup phase.

## Consequences

### Positive

- Single bug fix instead of three. Earnings-window, OI median, and IV fallback fixes land once and apply uniformly.
- A new screener variant is now ~150 lines of adapters plus a `ScreenerConfig`, not a 600-line copy.
- The type surface is enforced at the `ScreenerConfig` boundary; mismatched adapter signatures fail at import.
- Clean layering preserved per repo convention: adapters never import FastAPI types, never reach back into the runner, and never service-to-service back-call. Architecture review confirmed.

### Negative

- An indirection layer to read. Following a bug from a router to the line that does the math now passes through the runner and an adapter rather than a single service.
- Per-screener adapters currently live alongside the legacy bodies, so file lengths are temporarily worse before they get smaller. Resolved when `_legacy_process_*_symbol` bodies are deleted.
- Some semantic knobs that look uniform actually need per-config flags to preserve legacy behaviour. The `oi_delta_band_inclusive` flag exists solely because DITM's legacy code used `<=` while CSP and CC used `<`. Bit-for-bit parity required exposing this rather than picking one and moving on.

### Neutral

- `result_factory` returns `Any` because the result dataclasses (`CspResult`, `CcResult`, `DitmResult`) are not uniform. The runner is type-checked across this boundary as opaque; we accept the loss of static visibility in exchange for keeping result shapes screener-specific.
- `DitmResult` remains non-frozen specifically so the DITM `process_symbol` wrapper can apply `macro_hold` post-hoc from `macro_context` without re-threading `macro_context` through the runner signature. CSP and CC results stay frozen.

## Follow-ups

- [ ] Delete `_legacy_process_*_symbol` bodies once Phase 5/6 review confirms no fixture drift.
- [ ] Move the bottom-of-file adapter imports in [backend/services/cc_service.py](../../backend/services/cc_service.py) and [backend/services/ditm_service.py](../../backend/services/ditm_service.py) to the top of the file. There is no circular dependency, so the `# noqa: E402` annotations are unnecessary (reviewer minor #3).
- [ ] Consider rounding the tie-break and spread-percent denominators to 4 decimal places for strict bit-for-bit parity at arbitrary inputs (reviewer minor #4–#5). Currently dormant on the captured fixtures.
- [ ] Front-end mirror: the same `ScreenerConfig`-style consolidation on the React side is out of scope for this ADR and tracked separately.
- [ ] Write ADR-0001 (scoring-weights parameterisation). The number was reserved during Phase 1; the ADR itself has not yet been authored.
