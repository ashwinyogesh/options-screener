# Scoring Reference (CSP, CC, DITM) — v3.4

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
> at 5×. **v3.3** (May 2026) replaces the IV/HV Ratio factor (35 pts) in the CSP/CC ENV
> model with IV Percentile (IVP, 35 pts): raw IV/HV was noisy in low-liquidity names and
> double-counted HV already priced into the options chain. IVP measures where current IV
> stands in its own trailing distribution — a cleaner seller's-edge signal. **v3.4 — CSP
> Method D** (May 2026, see [ADR-0011](docs/adr/0011-method-d-csp-scoring.md)) is a
> CSP-only rebalance driven by a 7,085-trade backtest: SMA/SLP/RSI showed ρ(factor,
> realised ROC) ≤ 0 and were dropped; IVP raised 35→60; the 52W Trend curve was *flipped*
> (stocks far from highs gave better outcomes than stocks near highs); strike side
> rebalanced Δ 25→40, BA 25→15, ROC 35→30. CC scoring is unchanged from v3.3. The
> `SCORING_VERSION = "3.4.0"` constant gates all CSP env scoring. Diagnostic-preserved fields (`em_buffer_pct`, `dist_pct`, `otm_pct` for CSP/CC;
> `theta_annualized_pct`, `capital_efficiency_pct`, `breakeven_pct`, `hv_rank` for DITM) are
> still computed and returned in the response payload but contribute 0 to the score.
>
> **Empirical validation (2026-05-20, [ADR-0031](docs/adr/0031-csp-scoring-empirical-validation.md)).**
> A 12,751-trade synthetic-BS walk-forward backtest (2024-01 → 2026-04, full
> `MOMENTUM_UNIVERSE`) confirms the CSP scoring function is **monotone** in realised
> annualised ROC: bucket means go −6.3% → −4.2% → +3.0% → +13.4% → +15.6% across the
> 0-50 / 50-65 / 65-75 / 75-85 / 85-100 buckets. Spearman ρ(score, realised ROC) = +0.266
> (p ≈ 0). The 65-cutoff carries +14.0% mean ROC of separation. Factor independence:
> pairwise |r| within the trend cluster (Tr/SMA/SLP) maxes at 0.58 — below the audit's
> 0.6 "triple-count" threshold. IVP is the most independent factor in the system
> (max |corr| with any other factor = 0.08). Caveats: bull-regime sample; BA/LQ omitted
> in backtest (no historical chain data); HV(30) used as IV proxy. See the ADR for full
> methodology and limitations.

## Final score formula

```
final_score = 0.4 × env_score + 0.6 × strike_score
```

**Blend validation (2026-05, n=12,751 Method D trades).** A grid sweep of
α ∈ {0.0, 0.1, …, 1.0} where `final = α·env + (1−α)·strike` was scored against
realised annualised ROC. The rank-correlation maximum sits at α=0.30
(ρ=+0.479), and production α=0.40 (ρ=+0.475) is statistically
indistinguishable — bootstrap 95% CI on the difference is [−0.016, +0.022],
spanning zero. The entire α ∈ [0.20, 0.50] interval is on the same plateau.
α=0.40 was retained because it sits inside the rank-correlation plateau **and**
gives a stronger top-quartile mean ROC (+26.3%) than the rank-max α=0.30
(+24.4%) — a better profile for the "rank top-N, pick from the head" workflow
the screener is actually used for.

### CSP tiers — v3.4 Method D calibration (recalibrated 2026-05)

Recalibrated against an 18,016-trade backtest on the full 154-ticker universe
(3y, 35 DTE, weekly step, ρ(score, realised ROC) = +0.49). Bands are placed
at empirical cliff points in the decile distribution rather than evenly spaced.

| Score   | Label      | What you're getting                                                | Action                                              | Pop. share | Mean ROC | Win rate | Assign% | Mean $ PnL |
|---------|------------|--------------------------------------------------------------------|-----------------------------------------------------|-----------:|---------:|---------:|--------:|-----------:|
| ≥ 87    | Excellent  | Top decile — best win rate, lowest assignment, biggest $ PnL       | Take it, size up if conviction matches thesis      |       10%  |  +29.8%  |  90.8%   |  14.4%  |   +$365    |
| 79–86   | Strong     | Strong setup, mean ROC +17%                                        | Take it, normal size                                |       20%  |  +17.0%  |  86.2%   |  19.3%  |   +$200    |
| 69–78   | Good       | First profitable band — clear lift over middle pack                | Take it, understand the weakest factor              |       20%  |   +8.5%  |  83.2%   |  21.0%  |    +$44    |
| 51–68   | Marginal   | Mean ROC negative — high win rate masks tail assignment losses     | Only with a documented directional thesis          |       40%  |   −3.6%  |  78.2%   |  25.5%  |    −$94    |
| < 51    | Skip       | Bottom decile — worst $ PnL, drag outweighs premium                | Skip                                                |       10%  |   −4.1%  |  76.6%   |  26.5%  |   −$141    |

The "take it" threshold under v3.4 is **69**, not 65 — D5→D6 (score ≈ 69) is
the empirical cliff where mean ROC flips from −4% to +4% and mean $ PnL
crosses zero. Below 69 the high (~78%) win rate is misleading: a handful of
deep assignments wipe out months of small credits. The 65 gate in production
code remains a soft filter, but inside the 65–68 sub-band the edge does not
materialise without an overriding thesis.

D10 separation is the strongest signal: ≥ 87 captures only the top 10% but
delivers nearly double the mean ROC of the next band down and an assignment
rate that's 5 pts below it.

### CC tiers — v3.3 (recalibrated 2026-05)

Recalibrated against a 16,521-trade backtest on the full 154-ticker universe
(3y, 35 DTE, weekly step). Bands are chosen at empirical cliff points in the
decile distribution rather than evenly spaced.

| Score    | Label      | What you're getting                                                  | Action                                  | % pop | mean ROC | retain% | mean opp cost | phi  |
| -------- | ---------- | -------------------------------------------------------------------- | --------------------------------------- | ----- | -------- | ------- | ------------- | ---- |
| ≥ 83     | Excellent  | Top decile — best retention, lowest opportunity cost                 | Take it, size up if thesis matches      |  10%  |  +25.7%  |  82.8%  |    11.1       | +15  |
| 79–82    | Strong     | Phi crosses positive — upside preserved net of called-away cost      | Take it, normal size                    |  10%  |  +22.6%  |  77.6%  |    20.1       |  +3  |
| 72–78    | Good       | Clear lift over the middle pack (mean ROC jumps from 15% → 22%)      | Take it, understand the weakest factor  |  30%  |  +22.6%  |  73.6%  |    26.4       |  −4  |
| 56–71    | Marginal   | Noisy middle — score barely separates from random                    | Only with a directional thesis          |  35%  |  +13.7%  |  69.3%  |    32.1       | −18  |
| < 56     | Skip       | Worst retention (57%), highest opp cost, deeply negative phi         | Skip                                    |  15%  |  +11.8%  |  56.9%  |    36.7       | −25  |

The "take it" threshold under v3.3 is **72**, not 65 — D5→D6 is where mean
ROC jumps from 15% → 23% and opp_cost falls from 30 → 26. Below 72 the score
barely separates the deciles.

ρ(score, realised ROC) = +0.077, ρ(score, phi) = +0.110 on the full universe.
Method D was investigated for CC in May 2026 and **rejected** — it looked
strong on the original 109-ticker panel (in-sample optimism) but underperformed
v3.3 on the full 154-ticker universe and broke philosophy-fit monotonicity.

Both `env_score` and `strike_score` cap at 100, so `final_score` ∈ [0, 100].

---

## ENV score (max 100)

`compute_env_score(..., direction='csp'|'cc', iv_stale=False)` in
[backend/services/scoring/env.py](backend/services/scoring/env.py).

### CSP v3.4 Method D (ADR-0011)

CSP path drops SMA / SMA-Slope / RSI (each had ρ ≤ 0 against realised ROC in the
7,085-trade backtest) and re-allocates the freed weight onto IVP and 52W Trend.

| Factor             | Weight | Notes                                          |
|--------------------|-------:|------------------------------------------------|
| IV Percentile (IVP)|  60    | v3.3 curve × 60/35                             |
| Trend: 52W dist    |  20    | **flipped** — rewards distance FROM the high   |
| Chain Median OI    |  20    | unchanged                                      |
| Earnings in DTE    | −15    | penalty (unchanged)                             |
| **Total**          | **100**|                                                |

### CC v3.3 (unchanged)

| Factor             | Weight | CSP / CC differs? |
|--------------------|-------:|:-----------------:|
| IV Percentile (IVP)|  35    | no                |
| Trend: 52W dist    |  15    | **yes** (CC tent, CSP flipped)                |
| Trend: SMA align   |   5    | no (CC-only in v3.4)                          |
| Trend: SMA slope   |   5    | no (CC-only in v3.4)                          |
| RSI(14)            |  20    | **yes** (CC-only in v3.4)                     |
| Chain Median OI    |  20    | no                |
| Earnings in DTE    | −15    | no (penalty)      |
| **Total**          | **100**| (Earnings is a deductible penalty) |

### IV Percentile / IVP (35 pts) — v3.3

```
iv_percentile = percentile_rank(IV_30d, trailing_252d_window)
```

Measures where today's implied volatility sits in its own trailing one-year distribution.
A high percentile means options are priced rich — the seller's edge. Replaces IV/HV Ratio
(v3.2 and earlier) which was noisy in low-liquidity names and partially double-counted HV
already embedded in the options chain.

| Percentile   | Pts                |
|--------------|--------------------|
| < 30         | 0                  |
| 30–50        | linear 0 → 10      |
| 50–75        | linear 10 → 25     |
| 75–90        | linear 25 → 35     |
| ≥ 90         | 35                 |

`iv_percentile=None` (stale or unavailable) → **0 pts**. The `iv_hv_ratio` and `iv_stale`
parameters accepted by `compute_env_score()` are ignored in v3.3 and retained only for
call-site compatibility.

### Trend: 52W High Distance (15 pts) — direction-aware

v3.1 rescaled from 25 pts to 15 pts — the 10 pts freed are redistributed to SMA Alignment
(5 pts) and SMA50 Slope (5 pts). Smooth piecewise-linear curves; no step discontinuities.

```
dist       = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100
pct_below  = abs(min(dist, 0))
```

**CSP curve (v3.4 Method D — *flipped*: rewards distance FROM the high):**

| pct_below     | Pts                          |
|---------------|------------------------------|
| ≤ 5%          | 0                            |
| 5–30%         | linear 0 → 20                |
| > 30%         | **20** (flat top)            |

> v3.3 CSP curve was the inverse (15 pts flat top near the high, decaying to 0 at
> 30%). The flip was forced by a 7,085-trade backtest: ρ(Tr_old, realised ROC) = −0.29.
> See [ADR-0011](docs/adr/0011-method-d-csp-scoring.md).

**CC curve (v3.3 — unchanged):**

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

## CSP Strike score (max 100) — v3.4 Method D

`compute_csp_strike_score(...)` in [backend/services/scoring/strike.py](backend/services/scoring/strike.py).

| Factor               | Weight | v3.4 change vs v3.3 |
|----------------------|-------:|---------------------|
| Δ (delta position)   |  40    | **↑40**             |
| Bid-Ask Spread %     |  15    | **↓15**             |
| OI / Volume (per strike) | 15 | unchanged           |
| Annualized ROC       |  30    | **↓30**             |
| **Total**            | **100**|                     |

Curves are identical in shape to the v3.3 helpers — each per-strike point value is
rescaled by the new cap (e.g. Δ sweet spot 25 × 40/25 = 40 pts; BA ≤1% spread
25 × 15/25 = 15 pts). Internally `_score_delta_symmetric_methodd`, `_score_bid_ask_methodd`,
`_score_roc_methodd` wrap the v3.3 helpers with the scale factors.

### Δ (delta position) — 40 pts (v3.4 Method D)

Smooth piecewise-linear bell around `ideal_delta = -0.225`. Same band boundaries as
v3.3, all values rescaled × 40/25.

```
offset = abs(delta - (-0.225))
```

| offset                    | Pts                  |
|---------------------------|----------------------|
| ≤ 0.025                   | 40 (flat top)        |
| 0.025–0.075               | linear 40 → 25.6     |
| 0.075–0.125               | linear 25.6 → 14.4   |
| 0.125–0.175               | linear 14.4 → 0      |
| > 0.175                   | 0                    |

### Bid-Ask Spread % — 15 pts (v3.4 Method D)

```
spread_pct = (ask − bid) / mid × 100   where mid = (bid + ask) / 2
```

Same v3.3 curve rescaled × 15/25:

| Bucket    | Pts            |
|-----------|----------------|
| ≤ 1%      | 15             |
| 1–3%      | linear 15 → 10.2 |
| 3–5%      | linear 10.2 → 5.4 |
| 5–8%      | linear 5.4 → 1.2 |
| > 8%      | 0              |

### OI / Volume (per strike) — 15 pts (unchanged)

Per-strike circuit-breaker. Uses volume during US market hours, otherwise openInterest.

| Bucket      | Pts            |
|-------------|----------------|
| ≥ 1000      | 15             |
| 500–1000    | linear 10.5 → 15 |
| 200–500     | linear 6 → 10.5 |
| 100–200     | linear 0 → 6   |
| < 100       | 0              |

### Annualized ROC — 30 pts (v3.4 Method D)

```
capital_per_share = strike − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

For CSP, capital at risk = strike − credit (cash secured minus premium received). Same
v3.3 curve rescaled × 30/35 (ceiling 12% unchanged):

| ROC %       | Pts                  |
|-------------|----------------------|
| ≥ 12%       | 30 (ceiling)         |
| 8–12%       | linear 21 → 30       |
| 4–8%        | linear 12 → 21       |
| 2–4%        | linear 3 → 12        |
| 1–2%        | linear 0 → 3         |
| < 1%        | 0                    |

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

---

## Swing Trading Screener (SWING) — v2.0.0

See [ADR-0009](docs/adr/0009-swing-screener.md) for the original v1 design and
[ADR-0010](docs/adr/0010-swing-regime-engine.md) /
[ADR-0011](docs/adr/0011-swing-event-risk-scoring.md) /
[ADR-0012](docs/adr/0012-swing-hybrid-scoring.md) for the v2 hardening (regime engine,
event-risk scoring, hybrid additive + multiplicative composite).

### Composite score (max 100) — hybrid

```
raw   = R:R(40) + setup(30) + context(20) + institutional(10)
final = raw × regime_factor × earnings_factor × extended_factor
final = clamp(final, 0, 100)
```

- **Within a bucket**: addition (existing v1 maps, unchanged).
- **Across buckets**: multiplication (new in v2). Each multiplier floors strictly
  above zero so a punished score is still debuggable.

### Hard gates (applied BEFORE scoring)

| Gate | Threshold |
|------|----------:|
| Min price | $5.00 |
| Min average daily dollar volume | $20,000,000 |
| Min OHLC history | 60 bars |
| Min setup_score | 40 / 100 |
| Min R:R | **dynamic per regime** — see Regime Engine below |
| Stop distance | ≤ 50% of entry |
| Setup ∈ regime.disable_setups | excluded (e.g. reversion in `risk_off`) |
| Days-to-earnings ≤ 1 (any setup) | excluded |
| Days-to-earnings ≤ 7 AND setup = reversion | excluded |

### Regime engine (computed once per scan)

`backend/services/swing/regime.py`. See [ADR-0010](docs/adr/0010-swing-regime-engine.md).

`risk_on_score` (0–100) is a weighted composite of:

| Input | Weight | Source |
|-------|------:|--------|
| SPY trend (close vs EMA21 vs EMA50) | 35 | bull/neutral/bear → 100/65 or 35/0 |
| VIX 1y rolling percentile | 25 | <25p calm 100; <60p normal 70; <85p elevated 30; ≥85p shock 0 |
| Universe breadth (% > EMA50) | 25 | linear 0–100 |
| IWM/SPY 20d RS | 15 | ≤0.95 → 0; ≥1.05 → 100; linear |

Label and downstream effects:

| risk_on_score | regime_label | rr_gate | multiplier band | disable_setups |
|--------------:|--------------|--------:|----------------:|----------------|
| ≥ 65 | `risk_on` | 2.5 | linear → ~1.0 | (none) |
| 40 – 64.99 | `neutral` | 2.75 | ~0.76 – ~0.86 | (none) |
| < 40 | `risk_off` | 3.0 | 0.6 – 0.76 | `["reversion"]` |

`multiplier = 0.6 + 0.4 × risk_on_score / 100`, clamped to `[0.6, 1.0]`.

If any input fetch fails, the missing input falls back to neutral (50) and the
regime is returned with `degraded=True` instead of raising.

### Earnings multiplier (graduated)

`services.scoring.swing.earnings_factor(days_to_earnings)`. See [ADR-0011](docs/adr/0011-swing-event-risk-scoring.md).

| Days to earnings | Multiplier |
|------------------|-----------:|
| ≤ 3 | 0.50 |
| 4 – 7 | 0.75 |
| 8 – 14 | 0.90 |
| > 14, unknown, past | 1.00 |

If `days_to_earnings < hold_max_days`, the hold window is trimmed to `dte − 1` and
the result tags `forced_short_hold=True` (advisory; not an exclusion).

### Extended (chasing) multiplier

`extended_factor = 0.7` if current price is > 3% past the structural trigger
(`risk.SetupTrigger.extended`), else `1.0`. Per-setup triggers:

| Setup | Trigger |
|-------|---------|
| Breakout | base_high (consolidation top) |
| Momentum | EMA8 |
| Retest | reclaim_level |
| Reversion | current price (not chase-able by definition) |

### Setup detection (4 setups, 0–100 each, with within-setup multipliers)

The winning setup (highest score) becomes `setup_type`. Within-bucket multipliers
applied at the end of each detector:

| Setup | Hold | Strongest signals | Within-setup multiplier |
|-------|------|-------------------|-------------------------|
| Breakout | 5–10d | tight base (≥7d, range ≤8%), 1.5× volume surge, structure-high reclaim, BB squeeze <25p | `vol_factor = max(0.5, min(1.0, surge_ratio/1.5))` (no volume → 0.5×) |
| Momentum | 7–14d | EMA stack ≥7/9, ADX ≥22 with +DI dominant, RS vs SPY >1.1, MACD histogram zero-cross | `align_factor = max(0.6, min(1.0, ema_score/7))` (broken stack → 0.6×) |
| Reversion | 3–7d | RSI <30, Stochastic %K <20, bullish RSI divergence, Fib 0.618 hold | **hard floor**: `if price < EMA200 → score = 0` |
| Retest | 10–21d | structure_reclaim 5–20d ago, new consolidation base, RS holding ≥1.0 | `× 0.5` outside [5, 20] bars-since-reclaim window |

### R:R points (max 40, piecewise-linear)

| R:R | Points |
|----:|-------:|
| ≤ 2.5 | 0 |
| 3.0 | 25 |
| 4.0 | 35 |
| ≥ 5.0 | 40 |

### Setup points (max 30)

`setup_score × 0.30` (after within-setup multipliers above).

### Context points (max 20)

- **RS vs SPY (10)**: ≥1.2 → 10; 1.0–1.2 linear 5→10; 0.9–1.0 linear 0→5; <0.9 → 0.
- **EMA alignment (10)**: `ema_alignment.score / 9 × 10` (count of EMAs 8/21/50/200 below price + bonus when all four).

### Institutional points (max 10)

- **A/D line slope (5)**: ≥5% → 5; 0–5% linear; <0 → 0.
- **Held % institutions (5)**: ≥70% → 5; 40–70% linear; <40% → 0.

### Risk plan

- **entry** = per-setup structural trigger (see Extended multiplier table above).
- **stop** = `max(entry − 1.5 × ATR14, recent_10d_swing_low)` (tighter of the two wins;
  rejected if stop ≥ entry or risk > 50% of entry).
- **target** = `entry + R_mult[setup] × (entry − stop)` where `R_mult`: breakout 3.0,
  momentum 2.75, reversion 2.5, retest 3.25.
- **R:R** = `(target − entry) / (entry − stop)`; must be ≥ `regime.rr_gate`.

### Confidence tiers (post-multiplier)

| Tier | Conditions |
|------|------------|
| High | final ≥ 75 AND R:R ≥ 3.5 AND setup_score ≥ 70 |
| Medium | final ≥ 55 |
| Speculative | otherwise |

A regime/earnings haircut can demote a setup from `high` → `medium` → `speculative`.

### Universe

`swing_eligible` (~160 names) — statically curated for ≥$500M market cap and ≥500K
average daily share volume. Default universe for the Swing tab.
