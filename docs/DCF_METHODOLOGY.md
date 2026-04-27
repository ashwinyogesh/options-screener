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
  │     • gross margin, 3y operating margin trajectory, R&D %, SBC %
  │     • deferred-revenue YoY, ROIC
  │     • net buyback yield (gross repurchase − SBC dilution)
  │     • market multiples (forward P/E, EV/EBITDA, EV/Revenue)
  │     • risk-free rate from 10y Treasury, CAPM WACC build-up
  ▼
[2] LLM call (Azure OpenAI gpt-4.1, json_schema strict)
  │     Input:  full grounding payload + computed WACC
  │     Output: 3 scenarios (Y1 + Y5 margin, mid-growth), MC distributions,
  │             verdict, risks, drivers
  │     LLM does NOT supply WACC, only a small risk adjustment in bps.
  ▼
[3] Validation + clipping              ── reject hallucinated ranges,
                                          enforce tg ≤ mid_growth ≤ rg
  ▼
[4] Forecast horizon decision           ── 5y vs 10y based on growth profile
[5] Per-scenario fair value             ── deterministic Python
[6] Reverse DCF                          ── what growth does price imply?
[7] Sensitivity matrix                   ── 5×5 (WACC ±100bp × tg ±50bp)
[8] Monte Carlo (vectorized numpy, 5,000 trials default)
[9] Multiples cross-check                ── implied vs market P/E, EV/EBITDA, EV/Rev
[10] Franchise flag                       ── ROIC vs WACC, terminal-growth caveat
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
| `revenue_cagr_5y` | computed | Sanity check on growth assumption; gates 10y forecast |
| `operating_margin_ttm` | `info.operatingMargins` | Sanity check on margin |
| `operating_margin_3y` | financials | 3-year op-margin trajectory — distinguishes structural from cyclical |
| `gross_margin_ttm` | `info.grossMargins` | Structural profitability signal (vs OpEx-driven margin) |
| `rnd_pct_revenue` | financials | Identifies investment-phase names (high R&D = future leverage) |
| `sbc_pct_revenue` | cashflow | Real comp cost; feeds net-buyback math |
| `deferred_revenue_yoy` | balance sheet | Forward-bookings tell for SaaS / subscriptions |
| `roic_ttm` | NOPAT / (debt + book equity − cash) | Quality signal; drives franchise flag |
| `tax_rate` | `Tax Provision / Pretax Income` | Effective rate; falls back to 21% |
| `buyback_yield` (NET) | gross repurchase − SBC dilution | True per-share accretion (see §6) |
| `gross_buyback_yield` | share-count history | Pre-SBC repurchase pace |
| `sbc_dilution_yield` | SBC / market cap | Hidden dilution cost |
| `forward_pe`, `market_ev_ebitda`, `market_ev_revenue` | `info` | Inputs to the multiples cross-check (§10.5) |
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
revenue_growth        — Year-1 growth
operating_margin      — Year-1 operating margin
operating_margin_y5   — Year-5 operating margin (margin trajectory; see §9.1)
mid_growth            — Y6–Y10 fade target (used only when forecast = 10y; see §9.2)
wacc_risk_adj_bps     — integer offset around CAPM WACC
terminal_growth       — Gordon growth rate, capped at 4.5%
capex_pct_revenue     — flat 5-year average
rationale             — structured per-driver justification
strongest_driver      — single most material lever
narrative             — Damodaran-style story
```

### Constraints enforced by the prompt + validator

- **Monotonicity**: Conservative < Base < Optimistic for revenue growth. WACC adjustment runs the opposite direction.
- **Anchoring**: prompt commands LLM to cite the 5y CAGR, TTM operating margin, gross margin, R&D %, and beta. If base growth deviates >300bp from CAGR, must justify why.
- **Margin trajectory**: for investment-phase names with depressed TTM margin but healthy gross margin and elevated R&D, `operating_margin_y5` must reflect mid-cycle profitability, not TTM.
- **Mid-growth ordering**: validator enforces `terminal_growth ≤ mid_growth ≤ revenue_growth`.
- **Terminal growth ≤ 4.5%** — long-run nominal GDP. Anything higher is a category error.

### Why scenarios instead of point estimates?

A single FV is overconfident. Real uncertainty lives in the *spread*. The 3 scenarios become anchors for the Monte Carlo distributions — they aren't decorative; they constrain the simulation.

---

## 6. Net buyback yield — the silent value driver, done right

Most retail DCFs miss this. Mature compounders (AAPL, GOOG, META) shrink share count 2–4% annually. Ignoring this **understates per-share fair value by 10–20%** over 5 years. But growth names with heavy SBC (NVDA, CRWD, SNOW) often have *negative* net buybacks despite reporting big repurchase programs — the buybacks just offset SBC dilution. We compute both.

### How it's computed

```
gross_buyback_yield = -annual_change_in_shares  (from balance_sheet share count)
sbc_dilution_yield  = SBC_ttm / market_cap
net_buyback_yield   = gross_buyback_yield - sbc_dilution_yield   ← used in DCF
```

All three clipped to ±8%; the frontend shows them separately.

### How it enters the math

Effective share count decays each year using **net** buyback:

```
effective_shares = shares_out × average_of[(1 - net_bb)^t for t in 1..forecast_years]
fair_value_per_share = equity_value / effective_shares
```

Deliberate simplification — a fully accurate model would discount buyback FCF outflow inside FCFF. Doing the share-count adjustment instead is **simpler and almost exactly equivalent** for steady programs, and avoids double-counting capital allocation. Using *net* (not gross) prevents over-crediting growth names whose buybacks are running treadmill against SBC.

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

## 9. Forecast horizon, growth path, and margin trajectory

### 9.1 Margin trajectory (always)

Operating margin is **not** held flat. Each scenario specifies Year-1 and Year-5 margins; backend interpolates linearly Y1 → Y5, then holds flat through Y6–Y10 if the forecast extends. This matters most for investment-phase names where TTM margin is depressed but the bull thesis is operating leverage.

```
margin[t] = op_margin + (op_margin_y5 - op_margin) * (t-1) / 4    for t=1..5
margin[t] = op_margin_y5                                          for t=6..10
```

### 9.2 Forecast horizon decision (5y vs 10y)

```
if revenue_cagr_5y > 18%  OR  any scenario.revenue_growth > 20%:
    forecast_years = 10
else:
    forecast_years = 5
```

Growth-stage companies cannot be fairly valued in 5 years — too much value is in the explicit period beyond Year 5, but Gordon-growth at 4.5% will severely understate them. Extending to 10 years lets the explicit FCF capture the high-growth runway.

### 9.3 Two-stage growth path (10y forecasts only)

When `forecast_years = 10`, growth fades in two stages around `mid_growth`:

```
stage 1 (Y1–Y5):  rg → mid_growth      (linear)
stage 2 (Y6–Y10): mid_growth → tg      (linear)
```

For 5y forecasts, growth fades linearly from `revenue_growth` to `terminal_growth` and `mid_growth` is unused.

## 10. Monte Carlo — quantifying uncertainty

5,000 trials default; configurable 500–20,000.

### Distributions (LLM-supplied, clipped at runtime)

| Variable | Shape | Why |
|---|---|---|
| `revenue_growth` | normal(μ, σ) | Symmetric, plausible business-cycle errors |
| `operating_margin` | normal(μ, σ) | Year-1 margin |
| `operating_margin_y5` | normal(μ, σ) | Year-5 margin; sampled jointly with Y1 |
| `discount_rate` | triangular(low, mode, high) | Bounded; mode = CAPM WACC |
| `terminal_growth` | uniform(low, high) | Coarse — we don't pretend to know the shape |
| `capex_pct_revenue` | normal(μ, σ) | Same as margin |

Prompt explicitly tells the LLM: σ ≥ ½ × |Optimistic − Conservative|. Most models systematically understate volatility.

`mid_growth` is **not** sampled — it's a Base-scenario constant fed into 10y MC. Sampling it on top of `revenue_growth` and `terminal_growth` would explode variance for no information gain.

### Implementation — vectorized numpy

```python
# Margin path: linear Y1→Y5, flat thereafter
for t in range(n_years):
    margin_path[:, t] = om1 + (om5 - om1) * t/4 if t < 5 else om5

# Growth path: 5y linear OR 10y two-stage
if n_years <= 5:
    growth_path = rg + (tg - rg) * fade
else:
    stage1 = rg + (mid_g - rg) * fade1   # Y1–Y5
    stage2 = mid_g + (tg - mid_g) * fade2 # Y6–Y10
    growth_path = concat(stage1, stage2)

revenue_path = rev0 * cumprod(1 + growth_path)
fcf_path     = revenue_path * margin_path * (1-tax) - revenue_path * cx
pv           = (fcf_path / (1+dr)**yrs).sum(axis=1) + terminal_pv
```

20,000 trials run in **~0.1s**. No Python loops. LLM call dominates total latency.

### Outputs

Percentiles (P25/P40/P50/P60/P75), mean, std, `prob_above_current`, 30-bin histogram, 200-point downsampled scatter.

`prob_above_current` is the headline number for trade conviction.

## 10.5 Multiples cross-check

DCF and trading multiples should *rhyme*. When they don't, the disagreement is the thesis. We compute three implied multiples from the base scenario's fair value and compare to market.

```
Y1_revenue       = revenue_ttm × (1 + base.revenue_growth)
Y1_EBIT          = Y1_revenue × base.operating_margin   (Year-1 margin)
Y1_NOPAT         = Y1_EBIT × (1 - tax)
Y1_EBITDA        ≈ Y1_EBIT + Y1_revenue × capex_pct     (D&A ≈ capex steady state)

implied_fwd_pe        = base_FV / (Y1_NOPAT / shares)
implied_ev_ebitda     = base_EV / Y1_EBITDA
implied_ev_revenue    = base_EV / Y1_revenue
```

Delta vs market is color-coded: green |Δ| < 15%, yellow < 30%, red ≥ 30%. Backend also generates a one-line diagnostic identifying the largest disagreement.

### Why this matters

- |Δ| < 15%: DCF and tape agree. Higher conviction.
- DCF implies *lower* multiple than market: market expects more growth or longer duration than your scenarios. Either you're missing something or the stock is rich.
- DCF implies *higher* multiple than market: your assumptions are likely too bullish, OR market is pricing in real disconfirmation risk.

This is a credibility tax — you cannot publish a DCF that implies 80x P/E when the stock trades at 25x without explaining the gap.

## 10.6 Franchise flag (ROIC vs WACC)

Gordon-growth terminal value implicitly assumes new investments earn the cost of capital. For high-ROIC franchises (ROIC > 1.5× WACC), this **systematically understates value** — reinvestment at above-cost-of-capital returns is exactly what makes the franchise.

```
is_franchise = (roic_ttm > 1.5 × wacc) AND (terminal_growth < 3%)
```

When flagged, the frontend shows a yellow banner: *Consider checking sensitivity at terminal_growth = 3.5–4.5%.* This nudges the user toward the upper end of the GDP-cap range rather than punting to the LLM.

Secondary cases:
- ROIC < WACC: red warning (value-destroying growth; conservative growth assumptions justified).
- ROIC mid-range: neutral message (standard Gordon treatment OK).

---

## 10.7 Verdict — the trade-grade output

The verdict is **deterministic**: same grounding + same MC seed → same recommendation. Only `key_assumption_to_monitor` is LLM-authored.

| Field | Source | Formula |
|---|---|---|
| `suggested_entry_price` | computed | `P25 × (1 − MoS)` |
| `suggested_exit_price` | computed | `P75` |
| `margin_of_safety_pct` | computed | `(P25 − current) / current` |
| `data_quality_score` | computed | 0..1 from 8 binary checks |
| `confidence` | computed | `0.5 × spread_tightness + 0.5 × data_quality` |
| `recommendation` | computed | decision tree over price vs entry/P50/exit, gated by confidence; multiples disagreement amplifies AVOID |
| `key_assumption_to_monitor` | LLM | The single thesis-killer variable |
| `deterministic` | constant | `true` |
| `rationale` | computed | Short string: price vs thresholds + confidence + data quality |

### Margin-of-safety scaling

```
MoS = 5% + (1 − data_quality) × 10%   →   ranges 5%..15%
```

Higher MoS for low-confidence names (missing beta, sparse history, no buyback data, etc.) makes the recommended entry price more conservative.

### Data quality score (8 checks, equal weight)

1. `beta` present and not the default `1.0`
2. ≥ 4 years of revenue history
3. `tax_rate` present
4. `buyback_yield` present
5. `roic_ttm` present
6. `market_cap > 0`
7. `gross_margin_ttm` present
8. `operating_margin_ttm` present

### Spread tightness

```
spread_tightness = max(0, 1 − (P75 − P25) / P50)
```

Wide MC distributions (cyclicals, story stocks) penalize confidence even when data quality is full.

### Decision tree

```
if   price ≤ entry  and conf ≥ 0.6   → STRONG_BUY
elif price ≤ entry                   → BUY
elif price ≤ P50    and conf ≥ 0.55  → BUY
elif price ≤ P50                     → HOLD
elif price ≤ exit                    → HOLD
elif price ≤ exit × 1.10             → AVOID
else                                 → STRONG_AVOID

# Multiples cross-check amplifies bear-side calls:
if rec ∈ {HOLD, AVOID} and pe_delta_pct < −30%:
    rec → next bear tier (HOLD→AVOID, AVOID→STRONG_AVOID)
```

The `pe_delta_pct < −30%` guard fires when DCF FV implies a P/E far below the actual trading multiple — i.e. the market is paying a premium the cash-flow model can't justify. In that case, even a HOLD becomes an AVOID.

---

## 10.8 Dual-horizon comparison

Every request runs the full pipeline at **both** 5y and 10y horizons. The auto-picked `primary_horizon` (§9.4) populates the main panels; the other horizon shows up in the side-by-side comparison panel.

| Field | Description |
|---|---|
| `primary_horizon` | Which horizon (5 or 10) feeds the main UI panels |
| `horizon_5y`, `horizon_10y` | Full snapshot per horizon: base FV, P25/P50/P75, TV concentration, prob above current |
| `runway_value_pct` | `(FV_10y − FV_5y) / FV_5y` — how much explicit-period value the 5y model truncates |
| `tv_concentration_delta` | `TV_conc_5y − TV_conc_10y` — how much of fair value rides on the perpetuity vs explicit period |
| `diagnostic` | Plain-language reading of the runway pattern |

### Diagnostic buckets

| Pattern | Reading |
|---|---|
| `\|runway\| < 5%` | Mature; horizon doesn't matter. Perpetuity dominates. |
| `+5..+20%` | Modest runway premium. Some growth left in Y6–Y10. |
| `> +20%` | Significant runway. 5y model truncates the bull thesis. |
| `−5..−10%` | Mild fade in extended period. |
| `< −10%` | Strong fade signal — cyclical or peak-margin exposure. |

### Why this matters

A 5y horizon makes the **terminal-growth assumption** carry too much weight for high-growth names (TV concentration often 70%+). The 10y read forces the model to commit to an explicit medium-term margin path, surfacing fade risk that a clean perpetuity hides. Showing both lets the user see whether the verdict survives horizon choice or hinges on it.

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

1. **Quality scorecard** — ROIC vs WACC spread, reinvestment efficiency, moat score from LLM. Feeds into confidence beyond the current 8-check data-quality score.
2. **Consensus delta** — LLM Y1 revenue growth vs `info.revenueGrowth` analyst expectation.
3. **Cost of debt from real bond yields** — replace 5.5% default with issuer credit spread.
4. **Cyclical normalization** — for cyclicals, anchor base margin to mid-cycle, not TTM.
5. **Industry-specific terminal multiples** — alternative to Gordon-growth for franchise names where the 3% TG cap binds awkwardly.
