# Scoring Reference (CSP, CC, DITM) — v3.2

> Single source of truth for the screener scoring system. Every weight and threshold listed
> here is mirrored in code by `ENV_WEIGHTS` / `STRIKE_WEIGHTS` in
> [backend/services/scoring/config.py](backend/services/scoring/config.py) (CSP/CC) and the
> v3 scorer functions in [backend/services/ditm_service.py](backend/services/ditm_service.py)
> (DITM — kept inline pending a follow-up move to `services/scoring/`). The frontend
> `SCORE_LEGEND` arrays in [CspInput.tsx](frontend/src/components/CspInput.tsx),
> [CcInput.tsx](frontend/src/components/CcInput.tsx), and
> [DitmInput.tsx](frontend/src/components/DitmInput.tsx) mirror this document.

> **v3 lean model** for CSP/CC (May 2026, see [ADR-0007](docs/adr/0007-scoring-v3-lean-model.md))
> reduced the model from 14 → 8 factors. **v3.1 calibration** (May 2026, see
> [ADR-0009](docs/adr/0009-csp-cc-v31-calibration.md)) splits the Trend factor into three
> independent sub-signals (Tr 15 + SMA 5 + Slope 5 = 25 pts), smooths the Delta bell
> (steps → piecewise-linear, 20 → 25 pts), lowers Bid-Ask (30 → 25 pts), and lowers the
> ROC ceiling (20% → 12%). **DITM v3** (May 2026, see
> [ADR-0008](docs/adr/0008-ditm-v3-lean-model.md)) reduced the DITM model from 13 → 10
> factors, added the Leverage factor that was missing, and removed the HV Rank and Trend
> hard gates. **DITM v3.2** (May 2026) de-correlates the ENV momentum cluster: 200d Return
> compressed 25→15 pts, Trend Stability R² added at 10 pts, 52W Distance tent curve
> (exhaustion penalty at 0%), Delta sweet spot shifted to 0.82–0.90, Leverage hard-capped
> at 5×. Diagnostic-preserved fields (`em_buffer_pct`, `dist_pct`, `otm_pct` for CSP/CC;
> `theta_annualized_pct`, `capital_efficiency_pct`, `breakeven_pct`, `hv_rank` for DITM) are
> still computed and returned in the response payload but contribute 0 to the score.

## Final score formula

```
final_score = 0.4 × env_score + 0.6 × strike_score
```

Tiers (mirror the in-app legend in `CspInput.tsx` / `CcInput.tsx`):

| Score | Interpretation                              | Action                                       | Color  |
|-------|---------------------------------------------|----------------------------------------------|--------|
| ≥ 75  | All signals aligned, rare                   | Take it, normal size                          | green  |
| 65–74  | Solid trade with minor weakness             | Take it, understand the weakness              | light green |
| 55–64  | Mechanically fine, thesis-dependent         | Only if you have a directional view           | amber  |
| 45–54  | Something structural is off                 | Usually skip                                  | orange |
| < 45  | Multiple red flags                          | Skip                                          | red    |

The "take it" threshold is **65**: scores at or above 65 are tradeable on score alone
(modulo the would-I-own-it check); 55–64 require a documented thesis; below 55 the
structural drag outweighs the premium and the trade should be skipped.

Both `env_score` and `strike_score` cap at 100, so `final_score` ∈ [0, 100].

---

## ENV score (max 100)

`compute_env_score(..., direction='csp'|'cc', iv_stale=False)` in
[backend/services/scoring/env.py](backend/services/scoring/env.py).

| Factor             | Weight | CSP / CC differs? |
|--------------------|-------:|:-----------------:|
| IV / HV Ratio      |  35    | no                |
| Trend: 52W dist    |  15    | **yes**           |
| Trend: SMA align   |   5    | no (higher = more pts for both) |
| Trend: SMA slope   |   5    | no                |
| RSI(14)            |  20    | **yes**           |
| Chain Median OI    |  20    | no                |
| Earnings in DTE    | −15    | no (penalty)      |
| **Total**          | **100**| (Earnings is a deductible penalty) |

### IV / HV Ratio (35 pts)

```
iv_hv_ratio = yfinance_IV / HV_30d
```

Measures whether options are priced rich or cheap relative to actual recent movement.
IV > HV is the seller's edge. Recalibrated in v3 from 28 → 35 pts: with HV Rank dropped,
IV/HV becomes the primary volatility signal.

| Bucket      | Pts                |
|-------------|--------------------|
| < 0.8       | 0                  |
| 0.8–1.0     | linear → 5         |
| 1.0–1.1     | linear 5 → 12.5    |
| 1.1–1.2     | linear 12.5 → 22.5 |
| 1.2–1.3     | linear 22.5 → 35   |
| ≥ 1.3       | 35                 |

**Stale-IV flag:** trigger is `(IV is NaN) or (IV ≤ 0.01)`. When `iv_stale=True`, IV/HV pts
are forced to 0 and the row is annotated with `iv_stale: true` in the API response.

### Trend: 52W High Distance (15 pts) — direction-aware

v3.1 rescaled from 25 pts to 15 pts — the 10 pts freed are redistributed to SMA Alignment
(5 pts) and SMA50 Slope (5 pts). Smooth piecewise-linear curves; no step discontinuities.

```
dist       = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100
pct_below  = abs(min(dist, 0))
```

**CSP curve** (rewards strength near the 52W high — uptrend reduces put assignment risk):

| pct_below     | Pts                          |
|---------------|------------------------------|
| ≤ 5%          | **15** (flat top)            |
| 5–30%         | linear 15 → 0                |
| > 30%         | 0                            |

> v3 used a multi-segment decay (5→10, 10→20, 20→30). v3.1 uses a single linear
> decay from 15 at ≤5% to 0 at 30% — simpler and equally monotone.

**CC curve** (penalizes near-high — assignment risk; rewards 5–15% consolidation):

| pct_below     | Pts                          |
|---------------|------------------------------|
| ≤ 5%          | **0** (assignment risk)      |
| 5–15%         | linear 0 → 15                |
| 15–35%        | linear 15 → 0                |
| > 35%         | 0                            |

### Trend: SMA Alignment (5 pts) — v3.1 restored signal

Restored from v2 (dropped in v3 as redundant with 52W). v3.1 treats SMA alignment as an
independent structural signal separate from 52W proximity — the two are not redundant
(a stock can be near its 52W high with declining SMA50, or far from it with rising SMA50).

```
sma_ratio = SMA50 / SMA200
```

| sma_ratio     | Pts |
|---------------|----:|
| > 1.02        | 5   |
| 1.00–1.02     | 3   |
| 0.98–1.00     | 1.5 |
| < 0.98        | 0   |

### Trend: SMA50 Slope (5 pts) — v3.1 momentum confirmation

New in v3.1. Measures whether the 50-day moving average is accelerating (rising slope)
or decelerating (flat/declining). Rewards continuation, not just direction.

```
sma50_slope_pct = (SMA50[−1] / SMA50[−11] − 1) × 100   (10-trading-day window)
```

| slope_pct     | Pts                     |
|---------------|-------------------------|
| ≥ 0.5%        | 5 (full credit)         |
| 0.2–0.5%      | linear 3 → 5            |
| 0.0–0.2%      | linear 0 → 3            |
| < 0%          | 0 (declining SMA50)     |

### RSI(14) (20 pts) — direction-aware

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
| 42–62         | 20                 |
| 35–42         | linear 0 → 20      |
| 62–75         | linear 20 → 0      |
| < 35 or > 75  | 0                  |

> **Cliff fix #2:** v2 awarded a flat 2 pts for RSI 30–35, then jumped to 6 pts at exactly
> RSI=35 (a 4-pt cliff). v3 removes the 30–35 floor so the 35–42 ramp starts continuously
> at 0.

**CC curve** (sweet spot shifted lower; ceiling extended from 70 to 75):

| Bucket        | Pts                |
|---------------|--------------------|
| 38–58         | 20                 |
| 30–38         | linear 0 → 20      |
| 58–75         | linear 20 → 0      |
| < 30 or > 75  | 0                  |

> **Audit finding #8:** the v2 CC ceiling decay 58→70 was knife-edged, sending NVDA-style
> momentum names with RSI 72 to 0 pts. v3 extends the upper bound to 75 so AAPL/MSFT-style
> names in normal trends earn meaningful points.

### Chain Median OI (20 pts)

Median open interest across the working-delta band (puts: 0.10 < |Δ| < 0.40, calls
0.10 < Δ < 0.40):

```
pts = min(log10(OI) / log10(5000), 1.0) × 20
```

A circuit-breaker for illiquid chains. Saturates near 20 for any liquid name; gives
small-cap chains partial credit on a log scale. Rescaled from 8 in v2.

### Earnings penalty (−15 pts)

Binary flag — `True` if the company's next earnings announcement falls within the option's
DTE window. Applied as a flat deduction on top of the env score.

```
earnings_within_dte = True if 0 ≤ (earnings_date − today).days ≤ DTE
Source: yfinance calendarEvents.earnings
```

---

## CSP Strike score (max 100)

`compute_csp_strike_score(...)` in [backend/services/scoring/strike.py](backend/services/scoring/strike.py).

| Factor               | Weight |
|----------------------|-------:|
| Δ (delta position)   |  25    |
| Bid-Ask Spread %     |  25    |
| OI / Volume (per strike) | 15 |
| Annualized ROC       |  35    |
| **Total**            | **100**|

### Δ (delta position) — 25 pts (v3.1)

Smooth piecewise-linear bell around `ideal_delta = -0.225` (v3.1: raised from 20 pts,
step-bands replaced with continuous interpolation).

```
offset = abs(delta - (-0.225))
```

| offset                    | Pts                  |
|---------------------------|----------------------|
| ≤ 0.025 (Δ in [−0.25, −0.20]) | 25 (flat top)   |
| 0.025–0.075               | linear 25 → 16       |
| 0.075–0.125               | linear 16 → 9        |
| 0.125–0.175               | linear 9 → 0         |
| > 0.175                   | 0                    |

> **v3.1 change:** v3 used step bands (20/13/7/0) creating 7-pt cliffs at offsets
> 0.025, 0.075, and 0.125. v3.1 replaces with piecewise-linear interpolation through the
> same boundaries plus adds a 0.125–0.175 band (Δ −0.05 range that maps to 0).
> Max raised from 20 to 25 (rebalanced vs Bid-Ask lowered from 30 to 25).

### Bid-Ask Spread % — 25 pts (v3.1)

```
spread_pct = (ask − bid) / mid × 100   where mid = (bid + ask) / 2
```

Lower spread = better execution. Wide spreads erode realized premium on entry and every roll.

| Bucket    | Pts            |
|-----------|----------------|
| ≤ 1%      | 25             |
| 1–3%      | linear 25 → 17 |
| 3–5%      | linear 17 → 9  |
| 5–8%      | linear 9 → 2   |
| > 8%      | 0              |

> **v3.1 change:** lowered from 30 pts to 25 pts (rebalanced vs Delta raised to 25).

### OI / Volume (per strike) — 15 pts

Per-strike circuit-breaker. Uses volume during US market hours (9:30–16:00 ET weekday),
otherwise falls back to openInterest.

| Bucket      | Pts            |
|-------------|----------------|
| ≥ 1000      | 15             |
| 500–1000    | linear 10.5 → 15 |
| 200–500     | linear 6 → 10.5 |
| 100–200     | linear 0 → 6   |
| < 100       | 0              |

Rescaled from 5 in v2.

### Annualized ROC — 35 pts

```
capital_per_share = strike − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

For CSP, capital at risk = strike − credit (cash secured minus premium received).

| ROC %       | Pts                  |
|-------------|----------------------|
| ≥ 12%       | 35 (ceiling v3.1)    |
| 8–12%       | linear 24.5 → 35     |
| 4–8%        | linear 14 → 24.5     |
| 2–4%        | linear 3.5 → 14      |
| 1–2%        | linear 0 → 3.5       |
| < 1%        | 0                    |

> **v3.1 change:** ceiling lowered from 20% → 12%. Rationale: stable low-IV names
> (KO, JNJ, DUK) generate 5–10% annualised ROC at the configured ideal delta — under the
> old 20% ceiling they could never exceed 24.5 pts (70% utilisation). The new 12% ceiling
> lets them reach full credit at realistic premium levels. The tier boundaries are scaled
> proportionally.

The API response exposes the raw value as `roc_annualized`.

---

## CC Strike score (max 100)

`compute_cc_strike_score(...)` in [backend/services/scoring/strike.py](backend/services/scoring/strike.py).

| Factor               | Weight |
|----------------------|-------:|
| Δ (delta position)   |  25    |
| Bid-Ask Spread %     |  25    |
| OI / Volume          |  15    |
| Annualized ROC       |  35    |
| **Total**            | **100**|

### Divergences from CSP

The CC scorer uses the **same 8-factor structure, the same weights, and the same curves**
as CSP. Only two inputs differ:

1. **Δ ideal**: `+0.225` (sign-flipped from CSP). Smooth bell math is identical.
2. **ROC capital basis**: `current_price − credit`. The CC writer's capital at risk is the
   value of the underlying held to write the call, not the strike. The ROC scoring curve
   is otherwise identical to CSP.

All other factors (Bid-Ask, OI/Volume) are direction-agnostic and share the same code path
via shared helpers in `strike.py`.

---

## Diagnostic-only fields (not scored in v3)

The following fields continue to be computed and returned in the API response so the
frontend table columns remain populated, but they contribute **0 to the score**:

| Field           | What it shows |
|-----------------|---------------|
| `em_buffer_pct` | (0.5×EM-referenced) sigmas_outside × 100. Positive = strike outside the 0.5σ boundary. |
| `otm_pct`       | Raw `(S − K) / S × 100` for CSP, `(K − S) / S × 100` for CC. |
| `dist_pct`      | `None` in v3 (S/R was dropped). Kept as nullable field for response back-compat. |

These are visible in the strike table for context but do not influence ranking. ADR-0007
captures the rationale.

---

## Hard filters (gate before scoring)

These constraints filter candidates *before* scoring, not as scored factors:

| Filter | Source | Effect |
|--------|--------|--------|
| Delta gate | `CSP_CONFIG.delta_range = (-0.35, -0.10)` / `CC_CONFIG.delta_range = (0.10, 0.35)` | Strikes outside the gate are excluded |
| DTE window | User-supplied `min_dte` / `max_dte` | Expirations outside the window skipped |
| Capital gate (CSP only) | User-supplied `max_capital` | Strikes requiring `strike × 100 > max_capital` skipped (after OI aggregation — see [ADR-0005](docs/adr/0005-csp-capital-constraint.md)) |
| Stale IV | IV is NaN or ≤ 0.01 | IV/HV pts forced to 0 (row not dropped, just flagged) |

> **Future work** (not in v3): a hard filter on EM Buffer (reject candidates with
> `sigmas_outside < 0` against the 0.5×EM boundary) was considered to replace the dropped
> EM Buffer scored factor. Deferred — the delta gate already excludes most strikes that
> would fail this check at the configured ideal_delta. See ADR-0007 § Open questions.

---

## DITM (Deep-In-The-Money long calls) — v3.2

`compute_ditm_env_score(...)` and `compute_ditm_strike_score(...)` in
[backend/services/ditm_service.py](backend/services/ditm_service.py). DITM v3 (ADR-0008)
shipped after CSP/CC v3 — it applies the same lean philosophy plus DITM-specific fixes.
DITM v3.2 de-correlates the ENV momentum cluster (see `_score_trend_r2`, `_score_200d_return`,
`_score_52w_dist` docstrings in `ditm_service.py`).

### Final score formula

```
final_score = (0.5 × env_score + 0.5 × strike_score) × macro_mult
macro_mult  = 0.85 if macro_hold else 1.0
```

`macro_hold = (VIX ≥ 25 AND vix_5d_change > 0) OR (SPY < SMA200)`.

### DITM ENV (max 100)

| Factor | Weight | Notes |
|--------|-------:|-------|
| Trend Strength | 25 | Soft factor (no longer a hard gate) |
| 200d Return | 15 | ≥25% = full credit (v3.2: compressed from 25) |
| Trend Stability (R²) | 10 | OLS R² of 50-day price regression (v3.2 NEW) |
| 52W High Distance | 20 | tent peak 3–12% off highs (v3.2 tent curve) |
| Weekly RSI(14) | 15 | sweet 50–65 |
| Chain Liquidity | 15 | log10(median_OI) / log10(500) × 15 |
| Earnings (DTE-scaled) | up to −15 | Penalty, not gate |
| **Total** | **100** | (Earnings deductible) |

#### Trend Strength (25 pts)

```
P > SMA50 > SMA200       → 25
P > SMA50 only           → 15
SMA50 > SMA200 only      →  8
above SMA200 only        →  4
else                     →  0
```

> **Audit fix:** v2 used Trend < 22 pts as a hard gate that zeroed ENV when alignment
> wasn't full. v3 keeps Trend as the highest-weighted ENV factor (any partial alignment
> earns proportional pts) but no longer zeroes ENV for failing it.

#### 200d Return (15 pts) — v3.2 compressed

```
ret_200d = Close_today / median(Close[-205:-200]) − 1
```

Weight reduced 25→15 in v3.2 to break the momentum cluster: Trend Strength already
captures direction (25 pts), so awarding a further 25 pts for magnitude created a
dominant 50-pt momentum block. The freed 10 pts fund the new orthogonal Trend Stability
factor.

| pct      | Pts             |
|----------|-----------------|
| ≥ 25%    | 15              |
| 15–25%   | linear 11 → 15  |
| 5–15%    | linear 6 → 11   |
| 0–5%     | linear 1.5 → 6  |
| < 0%     | 0               |

#### Trend Stability R² (10 pts) — v3.2 NEW

```python
# 50-day OLS linear regression of closing price
_x      = [0, 1, …, n−1]   # n = min(50, available days)
coeffs  = np.polyfit(_x, Close[-50:], 1)
fitted  = np.polyval(coeffs, _x)
R² = 1 − SS_res / SS_tot
```

Measures *smoothness* of the trend — orthogonal to direction (Trend Strength) and
magnitude (200d Return). High R² means the stock drifted cleanly in one direction;
low R² means choppy/range-bound behaviour where theta bleeds a DITM long.

| R²       | Pts             |
|----------|-----------------|
| ≥ 0.85   | 10              |
| 0.70–0.85 | linear 7.5 → 10 |
| 0.50–0.70 | linear 4 → 7.5  |
| 0.30–0.50 | linear 1 → 4    |
| < 0.30   | 0               |
| NaN      | 5 (neutral default if < 10 days) |

#### 52W High Distance (20 pts) — **v3.2 tent curve**

`pct_below = abs(min(dist, 0))` where `dist = (Close − max_252d_close) / max_252d_close × 100`.

| pct_below | Pts             |
|-----------|-----------------|
| 0–3%      | linear 12 → 20  |
| 3–12%     | 20 (flat top)   |
| 12–25%    | linear 20 → 6   |
| 25–40%    | linear 6 → 0    |
| > 40%     | 0               |

> **v3.2 change (tent curve):** v3 gave full credit at 0% (right at the 52W high).
> v3.2 finds that buying right at a fresh local high carries exhaustion risk; the sweet
> spot is 3–12% off the high where momentum is confirmed but near-term reversal risk
> is lower. The v3 all-time-high case (0%) still earns 12 pts (not zero) to avoid
> discarding fundamentally strong stocks that briefly tag ATH.

#### Weekly RSI(14) (15 pts)

Resample daily Close to weekly (last close of each week), then Wilder RSI(14).

| Bucket            | Pts                        |
|-------------------|----------------------------|
| 50–65             | 15                         |
| 45–50 or 65–70    | 11                         |
| 40–45 or 70–75    | 6                          |
| 35–40 + Trend ≥ 18| 9 (pullback-entry credit)  |
| else              | 0                          |

#### Chain Liquidity (15 pts)

```
pts = min(log10(median_OI) / log10(500), 1.0) × 15
```

Reference is 500 (vs 5000 for CSP/CC) because DITM chains are structurally thinner than
ATM. Median is taken across the 0.60–0.95 delta band of the call chain.

#### Earnings penalty (DTE-scaled)

```
scale   = min(1, 30 / dte)
penalty = -15 × scale  if days_to_earnings ≤ 7
        = -7  × scale  if days_to_earnings ∈ [8, 14]
        = 0            otherwise
```

> **Audit fix #9:** v2 used `days ≤ 7 → ENV = 0` as a hard gate, which was a category
> error for long-dated trades. A 365-DTE LEAP losing its entire ENV because earnings is in
> 5 days ignores that the IV pop reverses within a week and 358 days of thesis remain.
> v3 scales by DTE: 7-day-out earnings on a 365-DTE LEAP costs ≈ −1.2 ENV; on a 30-DTE
> position, the full −15.

### DITM Strike (max 100)

| Factor | Weight | Notes |
|--------|-------:|-------|
| Δ (delta position) | 20 | sweet 0.82–0.90 (v3.2: shifted from 0.80–0.85) |
| **Leverage** | 25 | δ × price / mid · flat top 2.5–4.0× · hard 0 ≥5× (v3.2) |
| Extrinsic % | 25 | <2% = full |
| Bid-Ask Spread % | 20 | ≤2% = full |
| IV Percentile (inv) | 10 | ≤25th pct = full credit (cheap vol for buyers) |
| **Total** | **100** | |

#### Δ — 20 pts (v3.2: sweet spot shifted to 0.82–0.90)

| Bucket       | Pts                  |
|--------------|----------------------|
| 0.82–0.90    | 20 (flat top)        |
| 0.75–0.82    | linear 12 → 20       |
| 0.70–0.75    | linear 0 → 12        |
| 0.90–0.95    | linear 20 → 14       |
| 0.95–1.00    | linear 14 → 9        |
| < 0.70       | 0                    |

> **v3.2 change:** Sweet spot shifted from 0.80–0.85 → 0.82–0.90. Higher delta reduces
> gamma risk and makes the position more stock-like, more faithful to the stock-replacement
> thesis.

#### Leverage — 25 pts (v3.2: sharper cap)

```
leverage = delta × current_price / mid
```

The headline DITM metric. v3.2 tightens the upper cap: flat top extended to 4× (was 3.5×),
then a sharper linear drop to hard zero at 5× (was gradual decay to 8×). Leverage ≥5×
almost always reflects a mispriced or extremely wide-spread option.

| leverage   | Pts             |
|------------|-----------------|
| 0–1.5×     | linear 0 → 8    |
| 1.5–2.0×   | linear 8 → 17   |
| 2.0–2.5×   | linear 17 → 25  |
| 2.5–4.0×   | 25 (flat top)   |
| 4.0–5.0×   | linear 25 → 0   |
| ≥ 5.0×     | 0 (hard zero)   |

#### Extrinsic % — 25 pts

```
intrinsic     = max(price − strike, 0)
extrinsic     = max(mid − intrinsic, 0)
extrinsic_pct = extrinsic / strike × 100
```

| pct      | Pts                  |
|----------|----------------------|
| < 2%     | 25                   |
| 2–4%     | linear 25 → 19       |
| 4–6%     | linear 19 → 13       |
| 6–9%     | linear 13 → 5        |
| 9–12%    | linear 5 → 0         |
| > 12%    | 0                    |

> **Audit fix #4:** v2 had Extrinsic% (28) and Annualised Theta% (17) as separate
> factors. Because `θ_annual ≈ extrinsic / T` and the DTE filter holds T in a narrow
> band, the two are ~90% correlated. v3 keeps Extrinsic% (more directly meaningful to a
> buyer) and drops Theta%. Theta is still computed and returned for display.

#### Bid-Ask Spread % — 20 pts

```
spread_pct = (ask − bid) / mid × 100
```

| pct      | Pts                  |
|----------|----------------------|
| ≤ 2%     | 20                   |
| 2–4%     | linear 20 → 14       |
| 4–7%     | linear 14 → 7        |
| 7–12%    | linear 7 → 1         |
| > 12%    | 0                    |

#### IV Percentile (inverted) — 10 pts

```
iv_percentile = % of last 252d where HV < today HV  (HV-derived)
```

Buyers want low IV. v3 keeps IV Percentile as the single vol-cheapness factor; the v2 ENV
HV Rank factor was dropped (audit #5 — duplicate of this signal).

| pct      | Pts                  |
|----------|----------------------|
| ≤ 25     | 10                   |
| 25–50    | linear 10 → 7        |
| 50–75    | linear 7 → 3         |
| > 75     | 0                    |

### DITM hard filters (gate before scoring)

| Filter | Source | Effect |
|--------|--------|--------|
| Delta range | `DITM_CONFIG.delta_range = (0.70, 0.90)` | Strikes outside the gate are excluded |
| Strike side | strike < current_price | ITM calls only |
| DTE window | User-supplied min/max DTE (default 90–365) | Expirations outside the window skipped |
| ITM-only | `strike_filter` excludes OTM strikes | — |

All v2 hard gates that zeroed ENV are removed in v3:
- HV Rank > 50 → removed (audit #2)
- Trend < 22 → removed (Trend is now a 25-pt soft factor)
- Earnings ≤ 7d → replaced by DTE-scaled penalty (audit #9)

### DITM diagnostic-only fields (not scored in v3)

| Field | Notes |
|-------|-------|
| `theta_annualized_pct` | Computed and surfaced; was a v2 scored factor (audit #4 redundancy) |
| `capital_efficiency_pct` | `mid / price × 100` · was v2 scored (replaced by Leverage) |
| `breakeven_pct` | `(strike + mid − price) / price × 100` — display only |
| `hv_rank` | Symbol-level HV rank — was v2 ENV factor + hard gate (audit #2/#5) |
| `gap_3d_pct` | Max overnight gap last 3 sessions — alert flag, not scored |

---

## Endpoints accepting `max_capital` (CSP only)

| Endpoint                    | Parameter shape                                            |
|-----------------------------|------------------------------------------------------------|
| `POST /api/screener/csp`    | JSON body field `maxCapital` (float, optional)             |
| `GET /api/screener/csp/scan`| Query parameter `max_capital` (float, optional, `ge=100`)  |

The `/csp/scan` cache key includes `max_capital`:

```
cache_key = "{universe}:{top_n}:{min_dte}:{max_dte}:{max_capital}"
```

See [ADR-0004](docs/adr/0004-scan-result-caching.md) and
[ADR-0005](docs/adr/0005-csp-capital-constraint.md) for design rationale.

---

## Trade management — roll triggers, targets, and stops

The score ranks **entries**, not outcomes. A high-scoring trade still requires a
documented exit plan before capital is committed. These rules are not enforced by the
screener — they are the discipline layer the user is expected to apply.

### Why this section exists

Most assignment damage on otherwise-sound CSP/CC setups does not come from being wrong
about fair value. It comes from having no roll plan and freezing when the trade moves.
Writing the rules **before** entry converts an open-ended valuation question ("is this
strike a fair price to own?") into a finite rules-based plan ("if X happens, I do Y").

### Roll trigger — when to act

For a sold CSP, act when **any** of these fire:

| Trigger | Threshold | Why |
|---------|-----------|-----|
| Delta breach | abs(current Δ) ≥ **0.40** | Probability of assignment has roughly doubled vs. the −0.225 entry; the trade has materially moved against you |
| Price proximity | Spot within **2%** of strike | Gamma is accelerating; small further moves swing P&L sharply |
| Premium captured | **≥ 50%** of credit captured with **> 21 DTE** remaining | Lock the win and free the capital — diminishing return per remaining day |
| DTE floor | **≤ 21 DTE** with abs(Δ) ≥ 0.30 | Inside the gamma zone with assignment risk still elevated |
| Thesis break | Earnings pre-announce / structural support breach / sector-wide vol spike | Discretionary — the original setup no longer applies |

Mirror for sold CC: replace abs(Δ) ≥ 0.40 with Δ ≥ +0.40, and "spot within 2% of strike"
becomes spot within 2% **above** strike for CCs.

### Roll target — where to roll to

The default mechanic for a defensive roll is **down-and-out** (CSP) or
**up-and-out** (CC):

1. **Strike**: target the new strike at approximately **−0.225 Δ** in the next monthly
   expiry — i.e., re-center on the entry ideal at the new spot. Acceptable to roll to a
   strike between the current spot and the original strike if a credit is achievable;
   never roll to a tighter strike than current Δ supports.
2. **Expiry**: prefer the **next monthly** (typically +28 to +35 days from current expiry).
   Avoid weeklies on the roll — gamma is already the problem.
3. **Credit discipline**: only roll for a **net credit** (or zero cost). Rolling for a
   debit converts a defensive maneuver into a directional bet and almost always
   compounds losses. If no credit is available at any acceptable strike/expiry, the
   correct action is to **take assignment** and pivot to the wheel (run a CC against the
   shares).
4. **Liquidity check**: only roll into chains with bid-ask ≤ 5% and OI ≥ 200 at the new
   strike. The screener's per-strike OI/Volume factor (15 pts) is partly there to ensure
   rollability — if the original chain failed it, the trade should not have been entered.

### Stop — when to stop rolling

Rolling indefinitely is how a small loss becomes a structural one. Hard stops:

| Stop | Condition | Action |
|------|-----------|--------|
| Strike floor | Roll target would require a strike **> 15%** below original strike (CSP) or **> 15%** above original strike (CC) | **Take assignment.** The thesis has broken; pivot to the underlying. |
| Roll-count cap | **3 rolls** on the same position | **Take assignment.** Beyond this, you are managing a forced position, not an income trade. |
| Capital reallocation | Capital tied in defensive rolls **>2× the original CSP capital** | **Close at loss.** Free the capital for a fresh setup with a clean thesis. |
| No-credit roll | No acceptable strike/expiry combination yields a net credit | **Take assignment** (CSP) or **buy back at loss** (CC, if the underlying isn't held). |

### Pre-entry journal entry

Before placing the order, write down:

```
Symbol / strike / expiry / credit:
Roll trigger:                  e.g., abs(Δ) ≥ 0.40 or spot ≤ <X>
Roll target (if triggered):    e.g., next monthly at −0.225 Δ, must be net credit
Stop (when to stop rolling):   e.g., strike < <Y> or 3 rolls deep, then take assignment
Assignment plan:               e.g., wheel — sell CC at +0.225 Δ next month
```

The presence of this entry — not its specific values — is the discipline check. A trade
that cannot be specified at this level of detail before entry is a trade that should not
be placed, regardless of score.

> The screener does not enforce these rules. The 8-factor model ranks the entry; the
> exit plan is the user's contract with themselves. See ADR-0007 § Open questions for
> potential future automation (alerting on roll triggers).

---

## What changed in v3 (vs v2)

ADR-0007 captures the full rationale; this is the changelog summary.

**Dropped factors (6 total):**
1. **HV Rank (22 pts)** — correlated with IV/HV; structurally undervalued low-vol names
   (KO, PG, JNJ).
2. **SMA Alignment (15 pts)** — collapsed into Trend; redundant signal.
3. **DTE Sweet Spot (7 pts)** — should be a hard filter (already enforced via min/max DTE),
   not a scored factor.
4. **EM Buffer (20 pts)** — deterministic at the configured ideal_delta; inert signal that
   added redundancy with Δ and %OTM. Still computed for diagnostic display.
5. **%OTM from Spot (9 pts)** — deterministic function of Δ and IV; redundant with Δ.
   Still computed for diagnostic display.
6. **S/R Distance (18 pts)** — fragile swing-detection heuristic; high implementation cost
   for low signal value.

**Rescaled factors:**
- IV/HV: 28 → 35 (primary vol signal)
- Trend (52W direction-aware): 10 → 25 (absorbs SMA's role)
- RSI: 10 → 20
- Chain OI: 8 → 20
- Δ: 15 → 20 (now symmetric — fixes audit #7)
- Bid-Ask: 23 → 30
- OI/Volume: 5 → 15
- ROC: 10 → 35

**Cliff fixes (in surviving curves):**
- **#2 RSI-CSP**: removed 30–35 floor of 2 pts that created a 4-pt cliff at RSI=35.
- **#5 CC ≤5% near-high**: dropped from 4 pts to 0 (full penalty for assignment risk).
- **#6 ROC**: added 2–4% ramp to remove the small cliff at ROC=4.
- **#8 CC RSI ceiling**: extended upper bound from 70 to 75 for smoother decay.

**Audit-driven fixes:**
- **#7 Δ asymmetry**: aggressive and conservative wings now score equally at the same
  offset from ideal.
- **44%-redundancy stack**: removing EM Buffer + %OTM eliminates the v2 issue where Δ +
  EM + %OTM all measured the same delta-position signal at the configured ideal_delta.

**Weight integrity:** ENV totals 100 (35+25+20+20). Strike totals 100 (20+30+15+35).
