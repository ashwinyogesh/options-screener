# Scoring Reference (CSP & CC)

> Single source of truth for the screener scoring system. Every weight/threshold listed here
> is mirrored in code by the constants `ENV_WEIGHTS` and `STRIKE_WEIGHTS` in
> `backend/services/technical_service.py`. The frontend `SCORE_LEGEND` arrays in
> `CspInput.tsx` / `CcInput.tsx` mirror this document.

## Final score formula

```
final_score = 0.4 × env_score + 0.6 × strike_score
```

Tiers (unchanged from previous revision; recalibration deferred):

| Tier     | Range  | Color  | Meaning |
|----------|--------|--------|---------|
| Strong   | ≥ 70   | green  | All signals aligned, chain liquid, executable |
| Moderate | 45–69  | amber  | Most signals ok, some weakness in env or execution |
| Weak     | < 45   | red    | Poor IV env, execution risk, earnings overlap, or illiquid chain |

Both `env_score` and `strike_score` cap at 100, so `final_score` ∈ [0, 100].

---

## ENV score (max 100)

`compute_env_score(..., direction='csp'|'cc', dte=int|None, iv_stale=False)` in
`backend/services/technical_service.py`.

| Factor             | Weight | CSP / CC differs? |
|--------------------|-------:|:-----------------:|
| HV Rank            |  22    | no                |
| IV / HV Ratio      |  28    | no                |
| SMA Alignment      |  15    | no                |
| 52W High Distance  |  10    | **yes**           |
| RSI(14)            |  10    | **yes**           |
| Chain Median OI    |   8    | no                |
| DTE Sweet Spot     |   7    | no                |
| Earnings in DTE    | −15    | no (penalty)      |
| **Total**          | **100**| (Earnings is a deductible penalty) |

### HV Rank (22 pts)

> **Note:** Previously labeled "IV Rank" in the UI but always computed from 30-day HV ranked
> over 252 days (true ATM IV history is not stored). Renamed to "HV Rank" to reflect what is
> actually measured. Behavior unchanged besides rescale.

```
hv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100
HV      = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252
```

| Bucket       | Pts            |
|--------------|----------------|
| < 20         | 0              |
| 20–40        | linear → 6.6   |
| 40–60        | linear → 13.2  |
| 60–80        | linear → 18.33 |
| ≥ 80         | 22             |

### IV / HV Ratio (28 pts)

```
iv_hv_ratio = yfinance_IV / HV_30d
```

Recalibrated in v2: upper full-credit threshold compressed from 1.7 → 1.3. In a trending
bull market, IV/HV is structurally 0.9–1.15; the prior 1.7 threshold was calibrated for
post-spike/crisis conditions and earned only 3–9 pts in normal trending environments.

| Bucket      | Pts              |
|-------------|------------------|
| < 0.8       | 0                |
| 0.8–1.0     | linear → 4       |
| 1.0–1.1     | linear 4 → 10    |
| 1.1–1.2     | linear 10 → 18   |
| 1.2–1.3     | linear 18 → 28   |
| ≥ 1.3       | 28               |

**Stale-IV flag:** trigger is `(IV is NaN) or (IV ≤ 0.01)`.
When `iv_stale=True`, IV/HV pts are forced to 0 and the row is annotated with
`iv_stale: true` in the API response so the UI can surface the warning.

### SMA Alignment (15 pts)

| Condition                    | Pts |
|------------------------------|-----|
| Price > SMA50 > SMA200       | 15  |
| Price > SMA50 only           |  9  |
| SMA50 > SMA200 only          |  5  |
| else                         |  0  |

### 52W High Distance (10 pts) — direction-aware

```
dist       = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100
pct_below  = abs(min(dist, 0))
```

**CSP curve** (rewards proximity to the high — uptrend):

> Bug fix: the 5–10% segment previously started at 7.33 pts (creating a 2.67-pt cliff
> at exactly 5% below the 52W high). The segment now correctly starts at 10.0 pts and
> decays continuously through 7.33 at 10%, then to 0 at 30%.

| pct_below     | Pts                          |
|---------------|------------------------------|
| ≤ 5%          | 10                           |
| 5–10%         | linear 10 → 7.33             |
| 10–20%        | linear 7.33 → 4.67           |
| 20–30%        | linear 4.67 → 0              |
| > 30%         | 0                            |

**CC curve** (rewards mild consolidation, penalizes near-high — assignment risk):

| pct_below     | Pts            |
|---------------|----------------|
| ≤ 5%          | 4              |
| 5–15%         | linear 4 → 10  |
| 15–25%        | linear 10 → 6  |
| 25–35%        | linear 6 → 2   |
| > 35%         | 0              |

### RSI(14) (10 pts) — direction-aware

Wilder-smoothed RSI(14):

```
delta    = Close.diff()
avg_gain = EWM(alpha=1/14) of gains
avg_loss = EWM(alpha=1/14) of losses
RSI      = 100 − 100 / (1 + avg_gain / avg_loss)
```

**CSP curve:**

| Bucket        | Pts                |
|---------------|--------------------|
| 42–62         | 10                 |
| 35–42         | linear → 6         |
| 62–75         | linear → 0         |
| 30–35         | 2                  |
| < 30 or > 75  | 0                  |

**CC curve** (sweet spot shifted lower, ceiling decay steeper — overheated names blow
through call strikes):

| Bucket        | Pts                |
|---------------|--------------------|
| 38–58         | 10                 |
| 30–38         | linear 4 → 10      |
| 58–70         | linear 10 → 0      |
| < 30 or > 70  | 0                  |

### Chain Median OI (8 pts)

Median open interest across the working-delta band (puts: 0.10 < |Δ| < 0.40, calls
0.10 < Δ < 0.40):

```
pts = min(log10(OI) / log10(5000), 1.0) × 8
```

Effectively a circuit-breaker for illiquid chains; saturates near 8 for any liquid name.

### DTE Sweet Spot (7 pts) — new

Theta decay accelerates non-linearly approaching expiry; the 30–45 DTE band balances
gamma exposure against decay rate.

| DTE                            | Pts |
|--------------------------------|----:|
| 30 ≤ DTE ≤ 45                  | 7.0 |
| 21–30 or 45–60                 | 4.2 |
| 14–21 or 60–75                 | 2.1 |
| < 14 or > 75 (or unknown)      | 0   |

### Earnings in DTE (−15 pts)

```
earnings_within_dte = 0 ≤ (earnings_date − today).days ≤ DTE
```

If true, subtract 15 from the env score (can produce negative env contributions).

---

## CSP Strike score (max 100)

`compute_csp_strike_score(..., credit=float|None)` in `backend/services/technical_service.py`.

| Factor             | Weight |
|--------------------|-------:|
| Delta              |  15    |
| Dist vs Support    |  18    |
| Exp Move Buffer    |  20    |
| % OTM from Spot    |   9    |
| Bid-Ask Spread     |  23    |
| OI / Volume        |   5    |
| Annualized ROC     |  10    |
| **Total**          | **100**|

### Delta (15 pts)

Black-Scholes put delta. Sweet spot is −0.20 → −0.25 (≈ 20–25% ITM probability).

| Δ band                 | Pts    |
|------------------------|--------|
| −0.20 → −0.25          | 15     |
| ±1 absolute band       | 10     |
| −0.10 → −0.15          | 5      |
| < −0.30                | 5.83   |

### Dist vs Support (18 pts)

Volume-profile support, 6-month (126-day) lookback. Distance = nearest support level
below strike.

| Condition                              | Pts        |
|----------------------------------------|------------|
| ≤ 5% below strike                      | 18 → 10    |
| 5–10% below strike                     | 10 → 0     |
| > 10% below strike                     | 0          |
| All support above strike (uptrend)     | 7 (bonus)  |

### Exp Move Buffer (20 pts)

Recalibrated in v2: reference boundary changed from 1× EM to **0.5× EM**.
At the target delta (−0.225), the put strike sits approximately 0.25 EM units *outside*
the 0.5× boundary, earning full 20 pts. Under the prior 1× EM reference, the same
strike sat 0.25 EM units *inside* the boundary and earned 0 pts — a mathematical
certainty regardless of IV level (see ADR-0006).

```
EM              = S × σ × √(DTE/365)
EM_half_lower   = S − 0.5 × EM        (reference boundary)
sigmas_outside  = (EM_half_lower − strike) / EM
```

| sigmas_outside       | Pts |
|----------------------|----:|
| ≥ 0.2σ outside       | 20  |
| 0 to 0.2σ outside    | 13  |
| −0.1 to 0σ           |  5  |
| deeper inside        |  0  |

CC uses the symmetric upper boundary: `EM_half_upper = S + 0.5 × EM`.

### % OTM from Spot (9 pts)

```
otm_pct = (S − K) / S × 100
```

| Bucket    | Pts  |
|-----------|------|
| ≥ 15%     | 9    |
| ≥ 10%     | 6.75 |
| ≥ 5%      | 4.5  |
| ≥ 2%      | 1.5  |
| < 2%      | 0    |

### Bid-Ask Spread (23 pts)

```
spread_pct = (ask − bid) / mid × 100   where mid = (bid + ask) / 2
```

| Bucket    | Pts   |
|-----------|-------|
| ≤ 1%      | 23    |
| ≤ 3%      | 15.33 |
| ≤ 5%      | 8.52  |
| ≤ 8%      | 2.13  |
| > 8%      | 0     |

### OI / Volume (5 pts)

Per-strike circuit-breaker. Uses volume during US market hours (9:30–16:00 ET weekday),
otherwise falls back to openInterest.

| Bucket      | Pts                      |
|-------------|-------------------------:|
| ≥ 1000      | 5                        |
| 500–1000    | linear 3.5 → 5           |
| 200–500     | linear 2 → 3.5           |
| 100–200     | linear 0 → 2             |
| < 100       | 0                        |

Note: the 100–200 range awards partial credit (linear ramp) to borderline-liquid
strikes. The doc previously stated flat 0 below 200; the backend implementation
has always interpolated in this range — this entry corrects the documentation.

### Annualized ROC (10 pts)

```
capital_per_share = strike − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

Recalibrated in v2: full-credit threshold lowered from 30% → 20%. The prior 30%
threshold was only achievable on illiquid chains that simultaneously failed the Bid-Ask
factor. At the target delta (−0.225), liquid large-caps earn 8–17% annualized ROC;
20% for full credit makes the factor meaningful for the intended universe.

| ROC %       | Pts            |
|-------------|----------------|
| ≥ 20%       | 10             |
| 14–20%      | linear 7 → 10  |
| 8–14%       | linear 4 → 7   |
| 4–8%        | linear 1 → 4   |
| < 4%        | 0              |

The API response exposes the raw value as `roc_annualized`.

---

## CC Strike score (max 100)

`compute_cc_strike_score(..., credit=float|None)` in `backend/services/technical_service.py`.

| Factor               | Weight |
|----------------------|-------:|
| Delta                |  15    |
| Dist vs Resistance   |  18    |
| Exp Move Buffer      |  20    |
| % OTM from Spot      |   9    |
| Bid-Ask Spread       |  23    |
| OI / Volume          |   5    |
| Annualized ROC       |  10    |
| **Total**            | **100**|

### Delta (15 pts)

Black-Scholes call delta. Sweet spot is +0.20 → +0.25.

| Δ band                 | Pts    |
|------------------------|--------|
| +0.20 → +0.25          | 15     |
| ±1 absolute band       | 10     |
| +0.10 → +0.15          | 5      |
| > +0.30                | 5.83   |

### Dist vs Resistance (18 pts) — unchanged

Volume-profile resistance, 6-month (126-day) lookback.

```
nearest_R = min(resistances above current price)
gap_pct   = (nearest_R − strike) / strike × 100   (negative = R below strike)
```

| Condition                                   | Pts        |
|---------------------------------------------|------------|
| gap ≤ −20% (uncharted territory)            | 3          |
| −20% < gap ≤ −10%                           | 3 → 18     |
| −10% < gap ≤ 0%                             | 18         |
| All R ≤ strike, gap within 10% (ceiling stack) | +5 bonus |
| 0% < gap ≤ 5%                               | 18 → 10    |
| 5% < gap ≤ 10%                              | 10 → 0     |
| gap > 10%                                   | 0          |

### Exp Move Buffer (20 pts)

```
EM             = S × σ × √(DTE/365)
EM_upper       = S + EM
sigmas_outside = (strike − EM_upper) / EM
```

Same tier table as CSP but oriented to upside ceiling.

### % OTM from Spot (9 pts)

```
otm_pct = (K − S) / S × 100
```

Same tier table as CSP.

### Bid-Ask Spread (23 pts)

Same as CSP.

### OI / Volume (5 pts)

Same as CSP.

### Annualized ROC (10 pts) — new

CC capital basis = current price (simplification — does not track per-position cost basis).

```
capital_per_share = current_price − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

Tier table same as CSP.

---

## Capital constraint (CSP only)

The `maxCapital` parameter caps the collateral you are willing to commit per contract.
It is a **pre-scoring gate, not a post-hoc filter**: any strike that exceeds the cap is
skipped before scoring starts, so it never appears in results and does not inflate error
counts.

**Collateral formula:**

```
collateral = strike × 100   (one standard-lot contract)
```

A strike is evaluated only when:

```
strike × 100 ≤ maxCapital
```

### Defaults and validation

| Property   | Value                                          |
|------------|------------------------------------------------|
| Default    | `None` — no constraint, all strikes evaluated  |
| Floor      | $100 (rejects values below minimum 1-lot contract) |
| Applies to | CSP only — CC and DITM are unaffected          |

### Example

With `maxCapital = 8000`, only strikes ≤ $80 are evaluated:

```
$80 × 100 = $8,000 ≤ $8,000   ✓ evaluated
$81 × 100 = $8,100 > $8,000   ✗ skipped
```

### Endpoints

`maxCapital` is accepted by both CSP endpoints:

| Endpoint                    | Parameter shape                                            |
|-----------------------------|------------------------------------------------------------|
| `POST /api/screener/csp`    | JSON body field `maxCapital` (float, optional)             |
| `GET /api/screener/csp/scan`| Query parameter `max_capital` (float, optional, `ge=100`)  |

### Cache behaviour

The `/csp/scan` cache key includes `max_capital`:

```
cache_key = "{universe}:{top_n}:{min_dte}:{max_dte}:{max_capital}"
```

Scans with different `max_capital` values are stored as separate entries and do not
pollute each other. A scan with no capital constraint and one with `max_capital=8000`
each maintain an independent 30-minute TTL entry. See
[ADR-0004](docs/adr/0004-scan-result-caching.md) and
[ADR-0005](docs/adr/0005-csp-capital-constraint.md) for design rationale.

---

## What changed from the prior revision

1. **HV Rank rename** — was "IV Rank" but always derived from HV; now honestly labeled.
   Rescaled 30 → 22 to make room for new factors.
2. **IV / HV bumped** — 25 → 28; gives the genuine IV-vs-realized signal more weight.
3. **Stale-IV trigger fixed** — `IV < 0.15` (false positive on KO etc.) → `NaN or ≤ 0.01`.
   Now also surfaces an `iv_stale` flag in the API response.
4. **52W direction-aware** — CSP keeps reward-near-high curve (rescaled 15 → 10).
   CC gets a smooth-ramp consolidation curve (4 → 10 → 6 → 2 → 0) so near-high names
   correctly score lower for call selling.
5. **RSI direction-aware** — CC sweet spot moves from 42–62 to 38–58, ceiling decay
   sharpens (10 → 0 over 12 RSI pts vs 13).
6. **DTE Sweet Spot** — new 7-pt env factor rewarding the 30–45 DTE band where theta
   acceleration peaks.
7. **Chain OI bumped** — 5 → 8; gives small-cap chains more discrimination room.
8. **Δ rescale** — 18 → 15.
9. **Bid-Ask rescale** — 27 → 23.
10. **% OTM rescale** — 12 → 9.
11. **Annualized ROC** — new 10-pt strike factor; previously the strike score scored
    safety and execution but never the actual yield.

ENV totals to exactly 100 (22+28+15+10+10+8+7). Strike totals to exactly 100
(15+18+20+9+23+5+10). Confirmed by `assert sum(ENV_WEIGHTS.values()) == 100` and
`assert sum(STRIKE_WEIGHTS.values()) == 100` in the smoke test.

### v2 recalibration (quant-trader diagnostic)

12. **IV/HV upper threshold 1.7 → 1.3** — trending bull markets sustain IV/HV 1.1–1.3
    structurally; the prior 1.7 ceiling earned only 3–9 pts in normal conditions and
    essentially never fired. New curve: 0.8–1.0 → 4, 1.0–1.1 → 10, 1.1–1.2 → 18,
    1.2–1.3 → 28, ≥1.3 = 28.
13. **52W CSP curve bug fix** — the 5–10% segment previously started at 7.33 pts
    (discontinuous drop from 10 pts at 5%). Corrected to a smooth linear decay:
    5–10% → 10→7.33, 10–20% → 7.33→4.67, 20–30% → 4.67→0.
14. **EM Buffer reference 1×EM → 0.5×EM** — at the target delta (−0.225), the put
    strike sits ~0.25 EM units inside the 1×EM lower bound, earning 0 pts regardless
    of IV level. Changing the reference boundary to 0.5×EM puts the same strike 0.25 EM
    units *outside* the boundary, earning full 20 pts. See ADR-0006.
15. **ROC full-credit threshold 30% → 20%** — at target delta on liquid large-caps,
    annualized ROC is typically 8–17%. The prior 30% floor was only achievable on
    illiquid chains that simultaneously failed Bid-Ask. The factor was effectively
    inert. New tiers: ≥20%=10, 14–20%→7–10, 8–14%→4–7, 4–8%→1–4, <4%=0.
16. **OI/Volume doc corrected** — backend has always interpolated linearly in the
    100–200 range; documentation incorrectly stated flat 0 below 200. Table now shows
    the 100–200 ramp explicitly.
17. **`stable_csp` universe added** — 29 large-cap names across financials, payment
    networks, consumer defensives/staples, industrials, and healthcare. Curated for
    tight bid-ask spreads, structurally stable RSI (40–65), and IV/HV typically 1.0–1.3.
    See `backend/services/universe.py`.
