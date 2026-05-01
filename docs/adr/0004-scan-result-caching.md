# ADR-0004: Scan Result Caching: In-Process TTL + Client localStorage

- **Status**: Accepted
- **Date**: 2026-05-01

## Context

Universe scans (CSP / CC / DITM) fan out to 80+ `yfinance` calls via `asyncio.gather`, taking 25–30 s per run. Results were discarded on every page refresh, forcing users to re-run the full scan even when the data was minutes old. Two constraints shaped the solution space:

1. The app runs on a **single Azure Web App instance** — there is no Redis tier, no shared cache bus, and no APScheduler in the dependency tree.
2. `yfinance` is a best-effort scraper; unnecessary repeat calls waste quota and add latency.

A minimal caching layer was needed at both boundaries — server-side to absorb repeated identical API calls within a session, and client-side to survive page refreshes.

## Options Considered

1. **APScheduler — scheduled cron refresh every 30 min.**
   - Pros: results are always warm; no per-request latency spike.
   - Cons: wastes `yfinance` quota outside market hours; adds scheduler lifecycle (start / shutdown / error recovery) to the process; a crashed scheduler silently stops refreshing.
   - **Rejected.**

2. **Redis / external cache tier.**
   - Pros: survives server restarts; supports multi-instance deployments.
   - Cons: no shared infra today; adds a deployment dependency (provisioning, connection-string secrets, health checks) for a single-instance app where the benefit is theoretical.
   - **Rejected.**

3. **Server-Sent Events / WebSocket for live push.**
   - Pros: UI always shows fresh data without polling.
   - Cons: out of scope for this phase; requires protocol upgrade and a stateful server loop.
   - **Out of scope — deferred.**

4. **In-process `ScanCache` (plain dict + `time.monotonic` TTL) + client `localStorage` wrapper. (Chosen.)**
   - Pros: zero new dependencies; no scheduler lifecycle; pure-Python; testable via `clear()` / direct construction; backend warm cache returns in < 1 ms; page refreshes restore results instantly.
   - Cons: cache is lost on server restart (see Consequences); module-level singletons are a global; stampede risk under concurrent identical requests.
   - All trade-offs are explicitly bounded and acceptable at current traffic (see Consequences).

## Decision

Two complementary layers, both with a 30-minute TTL.

### Backend — `ScanCache`

A single `ScanCache` class lives in [backend/services/scan_cache.py](../../backend/services/scan_cache.py). Its internals are a plain `dict[str, _Entry]` where each entry carries a value and an `expires_at` timestamp produced by `time.monotonic()`. The TTL constant is `_TTL_SECONDS = 1800`.

Three module-level singletons are instantiated at import time — one per strategy:

```python
csp_scan_cache: ScanCache = ScanCache()
cc_scan_cache:  ScanCache = ScanCache()
ditm_scan_cache: ScanCache = ScanCache()
```

Each scan router (`csp.py`, `cc.py`, `ditm.py`) imports its singleton, builds a cache key from the query parameters, and calls `cache.get(key)` before dispatching `asyncio.gather`. On a miss the scan runs normally; the assembled `*Response` dataclass is stored via `cache.set(key, response)` before the router returns. On a hit, the cached response is returned directly with no further processing.

**Cache key shape** (CSP example):

```python
cache_key = f"{universe_key}:{top_n}:{min_dte}:{max_dte}"
```

The key encodes every query parameter that affects which symbols are scanned and which results are returned. `rf_rate` is intentionally excluded — see Consequences.

### Frontend — `resultCache.ts`

A thin localStorage wrapper lives at [frontend/src/utils/resultCache.ts](../../frontend/src/utils/resultCache.ts). It exports three functions:

- `saveResultCache<T>(key, data)` — serialises a `{ data, savedAt }` envelope and writes it under `screener:<key>`.
- `loadResultCache<T>(key, ttlMs?)` — reads the envelope, evicts on staleness (default TTL 30 min), returns `null` on miss.
- `clearResultCache(key)` — removes the entry.

Each strategy hook (`useCsp`, `useCc`, `useDitm`) calls `saveResultCache` after every successful scan and `loadResultCache` on mount to hydrate the UI instantly if a fresh entry exists. A "cached X min ago" notice is shown in the results-meta row so users know they are seeing cached output.

Write errors (quota exceeded, private-browsing restrictions) are silently swallowed; read errors return `null`. The cache is best-effort only.

## Consequences

**Positive**

- Repeat scans within 30 min return in < 1 ms on the server.
- Page refreshes restore the last result instantly without a network round-trip.
- Zero new runtime dependencies (no Redis, no APScheduler).

**Trade-offs accepted**

- `rf_rate` is **not** part of the backend cache key. The risk-free rate changes at most once daily; within a 30-minute TTL window this is inconsequential. If the TTL is extended beyond intraday in the future, the key should include the rate.
- Module-level singletons make the cache a process global. Tests that exercise cache-aware code paths must call `cache.clear()` in fixture teardown or construct a fresh `ScanCache` instance directly via `ScanCache()`.
- A cache stampede is theoretically possible when two identical scan requests arrive simultaneously during a cache miss. At current single-user traffic this is harmless. If concurrency increases to a point where stampedes are measurable, an in-flight futures map (keyed by `cache_key`) should be added inside the router.

**Limitation**

- The backend cache is **intentionally lost on server restart.** Serving stale option prices (potentially hours old) after a restart is worse than the cold-start penalty of one 25–30 s scan.

## Follow-ups

- [ ] If TTL is ever extended beyond 30 min, include `rf_rate` in the backend cache key.
- [ ] If per-request concurrency grows, add an in-flight futures map to prevent stampedes.
- [ ] Consider surfacing cache age in the backend response body so the frontend can display it without maintaining its own `savedAt` clock independently.
