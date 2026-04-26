# DCF Valuation — Methodology & Design Notes

This document explains how the **DCF Valuation** tab computes its numbers and **why** each design choice was made. Use it to audit results, defend trading decisions, or modify the model.

---

## 1. What this is (and isn't)

**This is**: a hybrid Damodaran-style DCF where the *judgment* (growth scenarios, narratives, verdict) comes from a frontier LLM (`gpt-4.1`) and the *math* (CAPM, percentile sampling, present-value arithmetic) is done deterministically in Python.

**This is not**:
- A replacement for fundamental research. It's a fast first-pass triage.
- An academic textbook DCF. It is opinionated toward **trade-grade outputs** (entry, exit, verdict).
- A pure formula. The verdict block is currently LLM-authored — see §10.

---

## 2. End-to-end pipeline

```
Ticker
  │
  ▼
[1] Grounding (yfinance + ^TNX)         ── all numeric, no LLM
  │     • price, shares, debt, cash, beta, revenue history, margins
  │     • risk-free rate from 10y Treasury
  │     • buyback yield from share-count history
  │     • CAPM WACC build-up (β + D/E + ERP)
  ▼
[2] LLM call (Azure OpenAI gpt-4.1, json_schema strict)
  │     Input:  full grounding payload + computed WACC
  │     Output: 3 scenarios, MC distributions, verdict, risks, drivers
  │     LLM does NOT supply WACC, only a small risk adjustment in bps.
  ▼
[3] Validation + clipping              ── reject hallucinated ranges
  ▼
[4] Per-scenario fair value             ── deterministic Python
[5] Reverse DCF                          ── what growth does price imply?
[6] Sensitivity matrix                   ── 5×5 (WACC ±100bp × tg ±50bp)
[7] Monte Carlo (vectorized numpy, 5,000 trials default)
  ▼
DcfResult JSON → frontend
```

Single LLM call per ticker. Everything else is deterministic.

---

## 3. Grounding — what comes from yfinance

The LLM gets **rich, real numbers**, not vague descriptions. This is the single biggest defense against hallucinated valuations.

| Field | Source | Why it matters |
|---|---|---|
| `current_price` | `info.currentPrice` | Anchor for upside %, verdict, reverse DCF target |
| `market_cap`, `total_debt`, `cash` | `info` | D/E weights for WACC |
| `beta` | `info.beta` | Cost of equity |
| `revenue_history` | `t.financials` | LLM must anchor base case to actual CAGR |
| `revenue_cagr_5y` | computed | Sanity check on growth assumption |
| `operating_margin_ttm` | `info.operatingMargins` | Sanity check on margin |
| `tax_rate` | `Tax Provision / Pretax Income` | Effective rate; falls back to 21% |
| `buyback_yield` | `balance_sheet` share count | Captures hidden EPS growth (see §6) |
| `risk_free_rate` | `^TNX` 10y Treasury | Live, not hardcoded |
| `wacc_buildup` | computed (CAPM) | Auditable, not LLM guesswork |

### Why yfinance and not Bloomberg / FactSet?
Free, no API key, broad coverage. Limitations: occasionally stale, rare delisted-ticker edge cases. Acceptable for screening. All values pass through `_safe_float` to reject NaN/Inf/None.

---

## 4. WACC — computed, not guessed

**Decision**: WACC is built deterministically in Python. The LLM is *given* the WACC and may only nudge it −100 to +150 basis points per scenario.

### Formula

$$\text{WACC} = \frac{E}{E+D} \cdot k_e + \frac{D}{E+D} \cdot k_d (1 - t)$$

with:
- $k_e = r_f + \beta \cdot \text{ERP}$
- $r_f$ = latest close of `^TNX` ÷ 100
- $\text{ERP} = 5.0\%$ — Damodaran's published US implied ERP, ~Apr 2026
- $\beta$ = yfinance value, clipped to [0.2, 3.0]; defaults to 1.0
- $k_d^{pretax} = 5.5\%$ default
- $t$ = effective tax rate from financials, falls back to 21%

WACC is then **clipped to [5%, 16%]**.

### Why this approach

| Alternative | Problem |
|---|---|
| Let LLM produce WACC outright | Drifts 100–300bp run-to-run. Discount rate is the most leverage-sensitive input. |
| Hardcode 8% / 10% / 12% | Ignores company-specific β and capital structure. |
| Damodaran Excel integration | Requires monthly XLSX scraping for 5 anchors — engineering cost > benefit. |
| **CAPM build-up in code (chosen)** | Reproducible, β/D/E company-specific, ERP & rf updated quarterly. LLM still has scenario discretion via the bps offset. |

---

## 5. Scenarios — where the LLM adds value

Three scenarios: **Conservative / Base / Optimistic**. The LLM supplies:

```
revenue_growth        — Year-1 growth; fades linearly to terminal_growth over 5 years
operating_margin      — flat across 5 years
wacc_risk_adj_bps     — integer offset around CAPM WACC
terminal_growth       — Gordon growth rate, capped at 4.5%
capex_pct_revenue     — flat 5-year average
rationale             — structured per-driver justification
strongest_driver      — single most material lever
narrative             — Damodaran-style story
```

### Constraints enforced by the prompt

- **Monotonicity**: Conservative < Base < Optimistic for growth/margin. WACC adjustment runs the opposite direction.
- **Anchoring**: prompt commands LLM to cite the 5y CAGR, TTM operating margin, and beta. If base growth deviates >300bp from CAGR, must justify why.
- **Terminal growth ≤ 4.5%** — long-run nominal GDP. Anything higher is a category error.

### Why scenarios instead of point estimates?

A single FV is overconfident. Real uncertainty lives in the *spread*. The 3 scenarios become anchors for the Monte Carlo distributions — they aren't decorative; they constrain the simulation.

---

## 6. Buyback yield — the silent value driver

Most retail DCFs miss this. Mature compounders (AAPL, GOOG, META) shrink share count 2–4% annually. Ignoring this **understates per-share fair value by 10–20%** over 5 years.

### How it's computed

From `t.balance_sheet` row "Ordinary Shares Number":

$$\text{annual\_change} = \left(\frac{\text{shares}_{last}}{\text{shares}_{first}}\right)^{1/n} - 1$$

$$\text{buyback\_yield} = -\text{annual\_change}$$

Positive = shares declining (buybacks). Negative = dilution. Clipped to ±8%.

### How it enters the math

Effective share count decays each year:

```
effective_shares = shares_out × average_of[(1 - bb)^t for t in 1..5]
fair_value_per_share = equity_value / effective_shares
```

Deliberate simplification — a fully accurate model would discount buyback FCF outflow inside FCFF. Doing the share-count adjustment instead is **simpler and almost exactly equivalent** for steady programs, and avoids double-counting capital allocation.

---

## 7. Reverse DCF — what is the market pricing in?

> "If you're paying $X today, what growth does the market need from this company for the deal to make sense?"

### Method

Bisection over `revenue_growth` ∈ [−10%, +50%], holding all other Base assumptions constant, until computed FV equals current price (within $0.01).

### Output interpretation

| Delta vs base | Reading |
|---|---|
| `implied > base + 200bp` | Market expects HIGHER growth; either growth must accelerate or stock is overvalued |
| `\|implied − base\| ≤ 200bp` | Fairly valued on this assumption |
| `implied < base − 200bp` | Market expects LOWER growth; if your thesis plays out, undervalued |
| no bracket found | Current price is outside achievable range |

### Why include this?

Forward DCF tells you "what's it worth if I'm right". Reverse DCF tells you "what does the market believe". If you and the market are within 200bp on growth, you don't have a thesis — you have an opinion on multiple expansion.

---

## 8. Sensitivity matrix — robustness check

5×5 grid: WACC ±100bp on one axis, terminal growth ±50bp on the other. Holds revenue_growth, margin, and capex from Base.

Constraint: terminal growth ≥ 0 and ≤ WACC − 50bp (Gordon model breaks down otherwise).

If small WACC moves swing fair value by >30%, your thesis is fragile. Tech-heavy growth names typically show this — long-duration cash flows are highly sensitive to discount rate. Frontend renders as green/yellow/red heatmap centered on the base cell.

---

## 9. Monte Carlo — quantifying uncertainty

5,000 trials default; configurable 500–20,000.

### Distributions (LLM-supplied, clipped at runtime)

| Variable | Shape | Why |
|---|---|---|
| `revenue_growth` | normal(μ, σ) | Symmetric, plausible business-cycle errors |
| `operating_margin` | normal(μ, σ) | Same logic |
| `discount_rate` | triangular(low, mode, high) | Bounded; mode = CAPM WACC |
| `terminal_growth` | uniform(low, high) | Coarse — we don't pretend to know the shape |
| `capex_pct_revenue` | normal(μ, σ) | Same as margin |

Prompt explicitly tells the LLM: σ ≥ ½ × |Optimistic − Conservative|. Most models systematically understate volatility.

### Implementation — vectorized numpy

```python
growth_path     = rg[:, None] + (tg[:, None] - rg[:, None]) * fade[None, :]
revenue_path    = rev0 * np.cumprod(1 + growth_path, axis=1)
ebit_path       = revenue_path * om[:, None]
fcf_path        = ebit_path * (1 - tax) - revenue_path * cx[:, None]
pv              = (fcf_path / (1 + dr[:, None]) ** yrs).sum(axis=1) + terminal_pv
```

20,000 trials run in **~0.1s**. No Python loops. LLM call dominates total latency.

### Why 5,000 trials?

Percentile error scales as $1/\sqrt{N}$. Empirically:

| N | P50 stability across runs |
|---|---|
| 1,000 | ±$1.5 |
| 5,000 | ±$0.7 |
| 20,000 | ±$0.3 |

5,000 is the sweet spot — smooth histograms, stable tails, no latency cost.

### Outputs

Percentiles (P25/P40/P50/P60/P75), mean, std, `prob_above_current`, 30-bin histogram, 200-point downsampled scatter.

`prob_above_current` is the headline number for trade conviction.

---

## 10. Verdict — the trade-grade output

Currently **LLM-authored**, with one computed field:

| Field | Source | Notes |
|---|---|---|
| `recommendation` | LLM | STRONG_BUY / BUY / HOLD / AVOID / STRONG_AVOID |
| `suggested_entry_price` | LLM | Typically near P25 with margin-of-safety discount |
| `suggested_exit_price` | LLM | Typically near P75 |
| `confidence` | LLM (0..1) | Lower for cyclicals/opaque accounting |
| `key_assumption_to_monitor` | LLM | The single thesis-killer variable |
| `margin_of_safety_pct` | computed | (P25 − current) / current |

### Known limitation

Entry / exit / confidence are **judgment calls**, not deterministic formulas. Two runs of the same ticker can yield different entry prices.

### Future hardening (not yet implemented)

```
entry_price    = P25 × (1 − margin_of_safety_required)
exit_price     = P75
base_conf      = 1 − (P75 − P25) / P50
data_quality   = sum_of(beta_present, ≥4y_revenue, real_tax_rate, buyback_data) × weights
confidence     = base_conf × data_quality
recommendation = decision_tree(current_price, entry, exit, confidence)
```

Keep `key_assumption_to_monitor` from the LLM — that's genuinely qualitative.

---

## 11. Why a single LLM call

A multi-call agentic flow (research → critique → revise) was rejected:

- Latency: 1 call ≈ 13s. 3 calls = 40s+.
- Cost: 3× the OpenAI bill for marginal gain.
- Determinism: more steps = more variance.
- The schema (`json_schema` strict) already enforces structure; a critic step can't fix bad numbers, only bad prose.

If the model produces garbage, the grounding-side validators clip it.

---

## 12. Schema-strict JSON output

Azure OpenAI `response_format = json_schema` (strict=True) gives:

- **No JSON parse errors** — model literally cannot return invalid JSON.
- **No missing fields** — `required` arrays enforced.
- **Type safety** — `wacc_risk_adj_bps` forced to `integer`.

Fallback to `json_object` mode exists but isn't being triggered on the current Azure deployment.

---

## 13. Caching policy (production)

**None.** Both the in-memory DCF cache and the disk-based supply-chain cache were removed before deploy:

- Single-instance App Service → cache scope = process lifetime anyway.
- Stale data is worse than slow data for valuation work — markets move.
- Re-running is cheap (~13s, ~$0.05).

If multi-instance deployment is added later, Redis with a 24h TTL is the right next step.

---

## 14. Rate limiting

Per-IP via `slowapi`:

| Route | Limit | Reason |
|---|---|---|
| `/api/dcf` | 5/min | LLM-heavy |
| `/api/supply-chain` | 3/min | 3 LLM calls per request |
| Default | 60/min, 600/hour | Generous for non-LLM endpoints |

Rate limiter runs **after** Pydantic validation — malformed requests don't burn budget.

---

## 15. Constants to review quarterly

| Constant | Current value | Source |
|---|---|---|
| `EQUITY_RISK_PREMIUM` | 5.0% | Damodaran implied ERP |
| `DEFAULT_RISK_FREE` | 4.5% | Fallback only; live value comes from `^TNX` |
| `DEFAULT_PRETAX_COST_OF_DEBT` | 5.5% | BBB corporate spread + rf |
| `MIN_WACC / MAX_WACC` | 5% / 16% | Sanity guardrails |
| `MC_TRIALS` | 5,000 | Stable percentiles, free latency |

---

## 16. Files

| Path | Purpose |
|---|---|
| `backend/services/dcf_service.py` | Full pipeline: grounding, LLM, math, MC |
| `backend/routers/dcf.py` | FastAPI route + rate limit |
| `frontend/src/types/dcf.ts` | TS contract |
| `frontend/src/hooks/useDcf.ts` | Fetch hook |
| `frontend/src/components/DcfView.tsx` | All UI panels |

---

## 17. Validation

Smoke-tested on AAPL @ $271 (Apr 2026):

- WACC: 9.74% (β=1.25, D/E≈4%, rf=4.5%)
- Base FV: $114.90
- MC P25=$98, P50=$111, P75=$126
- `prob_above_current` ≈ 0
- Verdict: HOLD, entry $220, exit $310

Percentile stability across 1k/5k/20k trials: ±$1–2. Verdict internally consistent (overvalued by all three lenses).

---

## 18. Open issues / future work

1. **Verdict determinism** (§10) — highest-leverage upgrade.
2. **Multiples cross-check** — compare base FV's implied forward P/E vs `info.forwardPE`; flag if delta > 30%.
3. **Quality scorecard** — ROIC vs WACC spread, reinvestment efficiency, moat score from LLM. Feeds into confidence.
4. **Consensus delta** — LLM Y1 revenue growth vs `info.revenueGrowth` analyst expectation.
5. **Cost of debt from real bond yields** — replace 5.5% default with issuer credit spread.
6. **Cyclical normalization** — for cyclicals, anchor base margin to mid-cycle, not TTM.
