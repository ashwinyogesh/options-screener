# Architecture

## What + why

The Options Screener is a two-artefact app — a stateless FastAPI backend and a React + Vite frontend — that scores option chains against three opinionated strategies (CSP, CC, DITM), surfaces a hidden DCF valuation tab, and visualises a curated supply-chain graph. This document is the layering and invariants reference for the codebase as it stands at HEAD `ac929af`, after the Phase 0–4 unified-screener refactor, the lint cleanup, and the pre-commit/CI install. Audience: a contributor returning to the code asking "where does what live, and what rules must I keep?". For methodology specifics, follow the links in [§10](#10-where-to-look); for build/run, see [README.md](../README.md).

## Section index

1. [High-level shape](#1-high-level-shape)
2. [Backend layering (strict)](#2-backend-layering-strict)
3. [The unified screener runner](#3-the-unified-screener-runner)
    - [3.1 The supply-chain package](#31-the-supply-chain-package)
    - [3.2 Scan result caching](#32-scan-result-caching)
4. [Scoring discipline](#4-scoring-discipline)
5. [Hidden but live: the DCF tab](#5-hidden-but-live-the-dcf-tab)
6. [Frontend layering (mirror)](#6-frontend-layering-mirror)
7. [Universe is curated](#7-universe-is-curated)
8. [Quality gates](#8-quality-gates)
9. [Hard rules (zero exceptions)](#9-hard-rules-zero-exceptions)
10. [Where to look](#10-where-to-look)

## 1. High-level shape

Two deployable artefacts:

- **Backend** — Python 3.12, FastAPI, deployed to **Azure Web App**. Stateless: no database, no in-process queue, no shared cache beyond per-process memoisation. External data sources are `yfinance`, SEC EDGAR, and Azure OpenAI; all live behind adapters in [backend/services/data_service.py](../backend/services/data_service.py) and [backend/services/options_service.py](../backend/services/options_service.py).
- **Frontend** — React 19, Vite, TypeScript (strict), deployed to **Azure Static Web Apps**. Hooks-only (no class components). All backend access goes over HTTP/JSON.

The two halves are independently deployable. The frontend ships from `frontend/` and consumes the backend by URL; the backend has no awareness of the frontend at runtime beyond CORS.

## 2. Backend layering (strict)

```text
HTTP request
  → backend/routers/*.py        (request/response shapes, validation, rate limiting)
    → backend/services/*.py     (domain logic, scoring, orchestration)
      → backend/services/data_service.py | options_service.py  (external data adapters)
        → yfinance / SEC EDGAR / Azure OpenAI
```

Rules — these mirror the contract in [.github/copilot-instructions.md](../.github/copilot-instructions.md) and are repeated here so they live next to the code map:

- **Routers must not contain business logic.** A router validates input, delegates to a service, and converts results (or domain exceptions) to HTTP. Anything richer than that belongs in `services/`.
- **Services must not import FastAPI types** — no `Request`, no `HTTPException`, no `Depends` inside `services/`. Services raise typed domain exceptions; routers map them to HTTP.
- **No service-to-router imports.** Ever.
- **Cross-service imports flow downward only.** A strategy service may use `data_service` and `options_service`; the data layer must never import a strategy service.

### Routers

Live under [backend/routers/](../backend/routers/). One per surface:

| File | Surface |
|------|---------|
| [csp.py](../backend/routers/csp.py) | Cash-secured put screener |
| [cc.py](../backend/routers/cc.py) | Covered call screener |
| [ditm.py](../backend/routers/ditm.py) | Deep-in-the-money long-call screener |
| [dcf.py](../backend/routers/dcf.py) | Discounted cash flow valuation (UI hidden — see [§5](#5-hidden-but-live-the-dcf-tab)) |
| [supply_chain.py](../backend/routers/supply_chain.py) | Curated supply-chain graph |

### Services

Live under [backend/services/](../backend/services/). The strategy services consume the data services; both are pure-Python modules.

| File | Role |
|------|------|
| [csp_service.py](../backend/services/csp_service.py) | CSP orchestration; exports `CSP_CONFIG`, thin `process_csp_symbol` wrapper |
| [cc_service.py](../backend/services/cc_service.py) | CC orchestration; exports `CC_CONFIG` |
| [ditm_service.py](../backend/services/ditm_service.py) | DITM orchestration; exports `DITM_CONFIG` |
| [dcf_service.py](../backend/services/dcf_service.py) | DCF valuation math |
| [supply_chain_service.py](../backend/services/supply_chain_service.py) | 14-line shim re-exporting `get_supply_chain` from the [supply_chain/](../backend/services/supply_chain/) package — see [§3.1](#31-the-supply-chain-package) and [ADR-0003](adr/0003-supply-chain-adapter-pattern.md) |
| [data_service.py](../backend/services/data_service.py) | OHLC + fundamentals adapter (yfinance / SEC) |
| [options_service.py](../backend/services/options_service.py) | Option-chain + IV adapter |
| [greeks_service.py](../backend/services/greeks_service.py) | Black–Scholes greeks |
| [indicators.py](../backend/services/indicators.py) | RSI, BB, SMA, HV, IV percentile, volume nodes |
| [universe.py](../backend/services/universe.py) | Single source of truth for tradable tickers — see [§7](#7-universe-is-curated) |

The `scoring/` and `screener/` sub-packages are documented in [§3](#3-the-unified-screener-runner) and [§4](#4-scoring-discipline).

## 3. The unified screener runner

The headline change in the most recent refactor and the **canonical pattern** for any future option-chain screener.

Three near-duplicate per-symbol services (CSP / CC / DITM, ~1,700 lines, ~95% structurally identical) were collapsed into one runner driven by a frozen configuration. Rationale, options weighed, and consequences are recorded in [ADR-0002](adr/0002-unified-screener-service.md).

### Package layout

[backend/services/screener/](../backend/services/screener/):

- [types.py](../backend/services/screener/types.py) — protocol types and dataclasses.
- [config.py](../backend/services/screener/config.py) — the `ScreenerConfig` dataclass.
- [runner.py](../backend/services/screener/runner.py) — the generic `run(...)` entry point.
- [__init__.py](../backend/services/screener/__init__.py) — public re-exports.

### Public type surface

All dataclasses are **frozen** unless explicitly noted otherwise:

- `Indicators` (frozen) — env-scoring inputs. Union bundle: contains every field any of the three live env scorers reads; unused fields default to `None`.
- `SymbolMetrics` (frozen) — render-only-ish per-symbol metadata (BB, SMA ratio, HV sigma, IV percentile, earnings date, gap-3d).
- `StrikeContext` (frozen) — per-strike inputs to the strike scorer. Union bundle, same shape rationale as `Indicators`.
- `StrikeBuildInputs` (frozen) — typed payload handed to `strike_context_builder`. Replaced an earlier untyped `dict`. Holds the candidate plus the per-symbol fields the builder needs to assemble a `StrikeContext`.
- `BaseStrikeResult` (**not frozen**) — minimum strike-row fields; `is_best` is mutated post-sort.
- `BaseScreenerResult` (**not frozen**) — minimum per-symbol result fields; concrete results (e.g. `DitmResult`) mutate post-hoc fields like `macro_hold` inside the `process_*_symbol` wrapper.

### `ScreenerConfig`

Frozen dataclass of pure-function hooks plus scalar knobs. The runner reads it; concrete screeners populate it. Anything less granular collapses back into per-screener `if direction == ...` branches inside the runner.

**Hooks (callable fields)**

- `chain_fetcher` — fetch the option-chain DataFrame for a symbol/expiration.
- `delta_fn` — Black–Scholes delta (put or call, per direction).
- `ohlc_fetcher` — wraps `data_service.get_ohlc`; per-screener so tests can patch independently.
- `iv_lookup` — wraps `options_service.get_implied_volatility`; same patch-isolation reason.
- `strike_filter` — OTM puts / OTM calls / ITM calls.
- `symbol_factory` — `(symbol, ohlc_df, current_price) → (Indicators, SymbolMetrics)`.
- `strike_context_builder` — `(StrikeBuildInputs, Indicators) → StrikeContext`.
- `env_scorer` — `(Indicators) → (score, detail)`.
- `strike_scorer` — `(StrikeContext) → (score, detail)`.
- `tie_break_key` — optional secondary sort key applied after `final_score`.
- `result_factory` — builds the screener-specific strike + result dataclasses from the runner's intermediate bundle.
- `pre_processors` — run on the indicator bundle after base computation, before scoring; DITM uses these for weekly-RSI and 200d-return enrichment.
- `candidate_delta_predicate` — optional gate applied to each candidate's delta after extraction and before the primary `delta_range` filter; DITM uses it to enforce `delta >= 0.60`.

**Scalars**

- `name` — `'csp' | 'cc' | 'ditm'`, used in logs.
- `direction` — `'short_put' | 'short_call' | 'long_call'`.
- `delta_range` — primary delta band.
- `ideal_delta` — fallback target when the band is empty.
- `oi_delta_band` — delta window over which `chain_median_oi` is computed.
- `oi_delta_band_inclusive` — `True` (DITM legacy `<=`) vs `False` (CSP/CC legacy `<`).
- `strike_sort` — `"asc"` (CC) or `"desc"` (CSP, DITM).
- `final_blend` — `(env_weight, strike_weight)`. `__post_init__` enforces a sum within `[0.99, 1.01]`. Live values: `(0.4, 0.6)` for CSP/CC, `(0.5, 0.5)` for DITM.

### Concrete configs and wrappers

Each strategy service exports exactly one `*_CONFIG` plus a thin `process_*_symbol` wrapper that calls `runner.run(config, ...)`:

- [csp_service.py](../backend/services/csp_service.py) → `CSP_CONFIG`, `process_symbol(...)`.
- [cc_service.py](../backend/services/cc_service.py) → `CC_CONFIG`, `process_cc_symbol(...)`.
- [ditm_service.py](../backend/services/ditm_service.py) → `DITM_CONFIG`, `process_ditm_symbol(...)`.

The wrappers exist (rather than the routers calling `runner.run` directly) so the public service signature stays stable across the refactor and so DITM can mutate `macro_hold` on the result without leaking that concern into the runner.

### Adapter purity

Every adapter referenced by `ScreenerConfig` is a **pure function of declared inputs**. Adapters never:

- import or call FastAPI types,
- reach into the runner (no closure over runner state),
- service-to-service back-call.

This is the property the refactor was designed to preserve; breaking it puts the runner-vs-services contract back in the same hole the legacy code was in.

### Legacy bodies

The pre-refactor per-symbol functions are retained in-file as `_legacy_process_symbol` / `_legacy_process_cc_symbol` for one-commit revert. They are **not wired in** — production calls go through the runner. They will be deleted in a follow-up cleanup phase once the new path has accumulated enough production miles.

Reference: [ADR-0002](adr/0002-unified-screener-service.md).

### 3.1 The supply-chain package

The supply-chain feature ships a sibling adapter-pattern package at [backend/services/supply_chain/](../backend/services/supply_chain/). It applies the same layering decision as the screener runner — one adapter per external boundary, pure helpers for I/O-free math, an orchestrator that composes them with declared dependencies — for a different problem shape (network + LLM rather than chain math).

#### Package layout

| File | Role |
|------|------|
| [types.py](../backend/services/supply_chain/types.py) | `CompanyNode`, `SupplyChainGraph`, `EightKFetchResult`; Pydantic models for the three LLM result shapes |
| [text_extraction.py](../backend/services/supply_chain/text_extraction.py) | Pure `extract_10k_relevant_text` / `extract_8k_text` (zero I/O, unit-testable without mocks) |
| [sec_client.py](../backend/services/supply_chain/sec_client.py) | `SecDataClient` — `httpx.Client` wrapper, tenacity retry on transport errors, `ThreadPoolExecutor` for parallel 8-K fetch, instance-level ticker→CIK cache |
| [llm_extractor.py](../backend/services/supply_chain/llm_extractor.py) | `LlmSupplyChainExtractor` — three Azure OpenAI passes (filing / industry / verifier), Pydantic validation per response |
| [pipeline.py](../backend/services/supply_chain/pipeline.py) | `get_supply_chain` orchestrator; merge / dedup helpers; graceful fallback when an LLM pass fails |

The legacy [supply_chain_service.py](../backend/services/supply_chain_service.py) is now a 14-line re-export shim so the router import path is unchanged.

#### Cross-cutting concerns

- **Retry policy** — `tenacity` (3 attempts, 0.5 → 4 s exponential backoff) on every SEC HTTP call. Retries only `httpx.TransportError` / `httpx.TimeoutException`; HTTP status errors propagate so the orchestrator can map 404s to `ValueError`.
- **LLM response validation** — Pydantic `extra="ignore"` models in `types.py`; `ValidationError` is wrapped as `RuntimeError`. Replaces the legacy silent-degradation behaviour where malformed JSON yielded a row with empty fields.
- **Parallel 8-K fetch** — `ThreadPoolExecutor(max_workers=4)` shares one `httpx.Client` across worker threads. Per-URL failures are counted on `SupplyChainGraph.eight_k_failed_count` rather than silenced; the frontend `MetadataBar` surfaces the count as a partial-corpus warning.
- **Frontend split** — pure layout math in [frontend/src/components/SupplyChain/layout.ts](../frontend/src/components/SupplyChain/layout.ts) is unit-tested under vitest's node environment. JSX helpers (`SourceBadge`, `nodeLabel`) and presentational subcomponents (`Legend`, `MetadataBar`, `NodeDetailPanel`) are siblings; the shell at [SupplyChainView.tsx](../frontend/src/components/SupplyChainView.tsx) shrank from 543 to ~190 lines.

Layering rules match the rest of the backend: adapters never import FastAPI types; `pipeline.get_supply_chain` raises `ValueError` / `RuntimeError`; the router maps to HTTP. The orchestrator declares its dependencies (`sec_client`, `llm`) as keyword-only parameters so tests inject fakes via the same seam as production.

Methodology: [docs/SUPPLY_CHAIN.md](SUPPLY_CHAIN.md). Decision record: [ADR-0003](adr/0003-supply-chain-adapter-pattern.md).

### 3.2 Scan result caching

Universe scans fan out to 80+ `yfinance` calls and take 25–30 s per run. Two complementary TTL caches (both 30 min) prevent redundant work:

- **Backend** — [backend/services/scan_cache.py](../backend/services/scan_cache.py) provides a `ScanCache` class (plain `dict` + `time.monotonic` TTL). Three module-level singletons (`csp_scan_cache`, `cc_scan_cache`, `ditm_scan_cache`) are imported by their respective scan routers, which check the cache before dispatching `asyncio.gather` and store the assembled response on a miss. The cache key encodes all query parameters that affect results; `rf_rate` is deliberately excluded (changes at most once daily — within a 30-min window this is inconsequential).
- **Frontend** — [frontend/src/utils/resultCache.ts](../frontend/src/utils/resultCache.ts) is a thin `localStorage` wrapper (`saveResultCache` / `loadResultCache` / `clearResultCache`). Each strategy hook hydrates from storage on mount if the entry is fresh; a "cached X min ago" notice is shown in the results-meta row.

The backend cache is intentionally lost on server restart — serving stale option prices across a restart is worse than a cold-start scan. No new runtime dependencies are introduced (no Redis, no APScheduler). Decision record: [ADR-0004](adr/0004-scan-result-caching.md).

## 4. Scoring discipline

- **Scoring constants are sacred.** The actual numbers — environment weights, strike weights, calibration thresholds — live in [backend/services/scoring/env.py](../backend/services/scoring/env.py) and [backend/services/scoring/strike.py](../backend/services/scoring/strike.py). They define the screener's identity. Changing them is an opinion change, not a refactor.
- **`scoring/config.py` is documentation-only.** The dicts in [backend/services/scoring/config.py](../backend/services/scoring/config.py) are not consumed by the math: the calibration curves are hardcoded inside the score functions in `env.py` / `strike.py`. This is a known gap. A future ADR (placeholder ADR-0001 — unwritten) will decide whether to parameterise the math against `config.py` or formally retire those dicts.
- **Methodology and code stay in lockstep.** Any change to scoring math requires updating [SCORING_REFERENCE.md](../SCORING_REFERENCE.md) and the frontend `SCORE_LEGEND` arrays in the **same PR**. This is non-negotiable — see [§9](#9-hard-rules-zero-exceptions).

## 5. Hidden but live: the DCF tab

[backend/services/dcf_service.py](../backend/services/dcf_service.py) and [backend/routers/dcf.py](../backend/routers/dcf.py) exist, are tested, and respond to live requests. The frontend tab — [frontend/src/components/DcfView.tsx](../frontend/src/components/DcfView.tsx) — is intentionally hidden in `App.tsx` pending verdict-calibration work.

**Do not delete this code.** Revisit when calibration lands. Methodology is in [docs/DCF_METHODOLOGY.md](DCF_METHODOLOGY.md).

## 6. Frontend layering (mirror)

```text
App.tsx → components/* → hooks/* → fetch()
```

Rules:

- **Hooks only.** No class components anywhere in [frontend/src/](../frontend/src/).
- **Components don't `fetch()` directly.** A component talks to a hook (`useCsp`, `useCc`, `useDitm`, `useDcf`, `useScreener`, `useSupplyChain`); the hook is the only thing that talks to the backend.
- **Strict TypeScript.** No `any` without an explicit justification comment on the same line or directly above. Strict optional checks are on.

The hook layer in [frontend/src/hooks/](../frontend/src/hooks/) maps 1:1 to backend routers. Types in [frontend/src/types/](../frontend/src/types/) mirror the JSON contracts; if the backend response shape changes, the type changes in the same PR.

## 7. Universe is curated

[backend/services/universe.py](../backend/services/universe.py) is the **single source of truth** for which tickers any screener can look at.

- Do not introduce parallel ticker lists in routers, hooks, scripts, or tests.
- The frontend mirror lives at [frontend/src/constants/universes.ts](../frontend/src/constants/universes.ts) — keep it in sync, but the backend list wins on disagreement.

The universe is curated by hand. Algorithmic expansion is out of scope; if you want a new symbol, add it explicitly.

## 8. Quality gates

Quality enforcement is local-first (pre-commit) plus CI-confirmed.

### Lint

- Tool: `ruff`. Config: [ruff.toml](../ruff.toml).
- Rule set: `E + F + W + I` — pycodestyle errors, pyflakes, warnings, import order.
- Documented ignores: `E402` (router imports must follow `load_dotenv` in `main.py`), `E702` (deliberate `score += p; bk['KEY'] = p` one-liner idiom across `scoring/`), `E501` (long lines in scoring tables / weight comments — the 120-char `line-length` covers the rest).
- Adding rules (`B`, `UP`, `SIM`, `mypy`) is a team decision, not a drive-by.

### Pre-commit

[.pre-commit-config.yaml](../.pre-commit-config.yaml) runs:

- `ruff` lint with autofix,
- `ruff-format` in **check-only** mode (informational; no autofix yet — formatting is a separate decision),
- standard hygiene hooks (trailing whitespace, end-of-file, YAML/JSON validity).

Install once per clone:

```pwsh
pre-commit install
```

### CI

[.github/workflows/quality.yml](../.github/workflows/quality.yml) runs on every push to `main` and every PR. It executes:

- `ruff` over the backend,
- `pytest` over the backend,
- `npm run build` over the frontend.

Concurrency cancels in-progress runs on the same ref so the latest commit wins. Deploy workflows ([deploy-backend.yml](../.github/workflows/deploy-backend.yml), [deploy-frontend.yml](../.github/workflows/deploy-frontend.yml)) are separate.

### Deferred

- Type-checking with `mypy` — not yet in CI or pre-commit.
- Frontend tests (`vitest`/RTL) — not yet wired.

Both are tracked in `/memories/session/plan-master.md`. New code should still be written test-ready: typed signatures, side-effect-injectable seams (this is exactly the shape `ScreenerConfig` enforces).

### Test layout

- [backend/tests/unit/](../backend/tests/unit/) — fast, in-process unit tests (`test_env_score.py`, `test_indicators.py`, `test_screener_config.py`, `test_strike_score.py`).
- [backend/tests/integration/](../backend/tests/integration/) — including [test_screener_baseline.py](../backend/tests/integration/test_screener_baseline.py).
- [backend/tests/fixtures/screener/](../backend/tests/fixtures/screener/) — captured manifest + per-(screener, ticker) outputs.

`test_screener_baseline.py` is the **bit-for-bit safety net** for any further screener changes. Nine characterization fixtures (3 tickers × 3 screeners) freeze the pre-refactor outputs. If you change the runner, `ScreenerConfig`, or any adapter and this test still passes, you have not changed observed behaviour. If it fails, either you intended a behaviour change (re-capture and document why) or you have a bug.

## 9. Hard rules (zero exceptions)

Restated from [.github/copilot-instructions.md](../.github/copilot-instructions.md) — these are non-negotiable:

- **No secrets in source.** Use environment variables. [backend/.env.example](../backend/.env.example) is the contract — if you add a new env var to the code, update the example file in the same PR.
- **No new top-level dependencies without justification** in the PR description. Both `backend/requirements.txt` and `frontend/package.json`.
- **No silent network calls in tests.** Always mock `yfinance`, SEC EDGAR, and Azure OpenAI. A test that hits a live API is a broken test.
- **Methodology and code stay in lockstep.** If math changes, the doc changes in the **same PR**. CI will eventually enforce this; until then, reviewers do.
- **Conventional commits.** `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`. Multi-paragraph bodies for non-trivial changes.

## 10. Where to look

| Topic | Path |
|-------|------|
| Scoring math | [SCORING_REFERENCE.md](../SCORING_REFERENCE.md) |
| DCF math | [docs/DCF_METHODOLOGY.md](DCF_METHODOLOGY.md) |
| Supply-chain extraction | [docs/SUPPLY_CHAIN.md](SUPPLY_CHAIN.md) |
| Architectural decisions | [docs/adr/](adr/) |
| Build + run | [README.md](../README.md) |
| Project conventions | [.github/copilot-instructions.md](../.github/copilot-instructions.md) |
| Agent team | [.github/agents/](../.github/agents/) |
| Per-language style | [.github/instructions/](../.github/instructions/) |

Active session plans (`/memories/session/plan-*.md`) capture in-flight work and supersede this doc on points where they disagree about future direction — but never about the shipped state, which is what this file describes.
