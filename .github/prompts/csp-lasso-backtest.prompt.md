---
mode: agent
description: >
  Build and run a Lasso regression backtest on the CSP options backtest dataset
  to identify the highest-influence raw factors driving realised annualised ROC.
  Produces coefficient tables, reliability curves, and actionable scoring recommendations.
---

# CSP Lasso Factor-Importance Backtest

## Context

You are working in the Options Screener repo (`c:\Users\ashwincha\Options`).
The primary CSP backtest dataset is **`csp_backtest_full_v2.csv`** (repo root),
18 016 synthetic-BS walk-forward trades, 154-ticker universe, 35 DTE, weekly
entry step, 2023-01 → 2026-04.

Column inventory:

| Column | Type | Meaning |
|---|---|---|
| `scan_date` | date | Trade entry date |
| `ticker` | str | Underlying symbol |
| `spot` | float | Stock price at entry |
| `strike` | float | Put strike |
| `dte` | int | Days to expiration |
| `hv30` | float | 30-day realised HV (annualised, 0–1 scale) |
| `iv_pct` | float | IV percentile (0–100) in trailing 252-day window |
| `rsi` | float | RSI(14) |
| `sma_ratio` | float | Close / SMA50 |
| `sma50_slope_pct` | float | SMA50 slope as % of price per day |
| `dist52w` | float | (Close − 52W-High) / 52W-High × 100 (≤ 0, further = more negative) |
| `delta` | float | Put delta (−1 to 0, reported as negative value) |
| `premium` | float | Put mid price in dollars |
| `env_IVP` | float | Scored IVP component (0–60 under Method D) |
| `env_Tr` | float | Scored 52W Trend component (0–20, Method D flipped) |
| `strike_Delta` | float | Scored delta component (0–40) |
| `strike_ROC` | float | Scored ROC component (0–30) |
| `env_score` | float | ENV sub-score (0–100) |
| `final_score` | float | Composite score (0–100), `0.4×env + 0.6×strike` |
| `spot_at_exp` | float | Spot price at expiration |
| `assigned` | bool/int | 1 if put was assigned (spot_at_exp < strike) |
| `pnl_per_contract` | float | Dollar PnL per 100-share contract |
| `realised_roc_annualised` | float | Annualised ROC on put contract (primary outcome) |
| `realised_return_per_dollar` | float | Return per dollar of capital committed |

**Note:** `env_SMA`, `env_SLP`, `env_RSI`, `env_OI` are all zero in this
dataset — Method D dropped those factors. Do not use them as features.

---

## Task

Write and execute `scripts/csp_lasso_factor_analysis.py` that performs the
following steps end-to-end, printing a clean report to stdout and saving a
results CSV to `csp_lasso_results.csv` in the repo root.

---

## Step 1 — Feature Engineering

Starting from the raw columns, derive the following candidate features.
Each derivation must be documented with a one-line comment explaining the
intuitive CSP rationale.

### Volatility / premium features (most likely to dominate)

```
hv30_pct          = hv30 * 100                     # annualised HV as percentage
iv_minus_hv       = iv_pct / 100 - hv30            # vol risk premium (IV > HV = richer selling edge)
iv_hv_ratio       = (iv_pct / 100) / (hv30 + 1e-6) # ratio form of vol premium
annualised_yield   = (premium / (strike - premium)) * (365 / dte) * 100
                                                    # raw annualised put yield — what the scorer's ROC ceiling is capping
premium_pct_spot  = premium / spot * 100            # premium as % of stock price (size-normalised)
```

### Moneyness / structural features

```
otm_pct           = (spot - strike) / spot * 100    # how far OTM the put is (>0 = OTM)
delta_abs         = abs(delta)                      # absolute delta (0→1); 0.20 typical CSP delta
delta_sq          = delta_abs ** 2                  # captures nonlinear assignment risk near ATM
```

### Trend / momentum features

```
dist52w_abs       = abs(dist52w)                    # magnitude of distance from 52W high (Method D rewards this)
near_high_flag    = (dist52w_abs <= 5).astype(int)  # binary: stock within 5% of 52W high
far_from_high_flag= (dist52w_abs >= 30).astype(int) # binary: stock >30% below high (Method D sweet spot)
sma_above         = (sma_ratio >= 1.0).astype(int)  # price above SMA50 (trend positive)
rsi_neutral       = ((rsi >= 40) & (rsi <= 60)).astype(int)  # RSI in neutral band
rsi_overbought    = (rsi >= 70).astype(int)         # overbought — assignment risk if reversal
```

### Interaction features (capture joint effects)

```
iv_pct_x_dist52w  = iv_pct * dist52w_abs            # high IV + far from high = Method D core thesis
hv30_x_delta_abs  = hv30 * delta_abs                # volatility × moneyness — joint assignment risk
yield_x_otm       = annualised_yield * otm_pct      # yield × cushion — efficiency of the trade
```

### Include the scored components directly as well

`env_IVP`, `env_Tr`, `strike_Delta`, `strike_ROC`, `env_score`, `final_score` —
to see how the engineered scoring curves compare against raw linear features.

---

## Step 2 — Target Variables

Run the Lasso analysis against **two targets** separately:

1. **`realised_roc_annualised`** — continuous primary outcome, winsorised at
   the 1st and 99th percentiles before fitting to reduce the influence of
   extreme assignments.

2. **`assigned`** (binary, 0/1) — using `LogisticRegression(penalty='l1',
   solver='liblinear')` to find which features predict put assignment. This
   isolates the *risk* dimension from the *return* dimension.

---

## Step 3 — Preprocessing

```python
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
```

- Impute the ~200 missing `iv_pct` and ~150 missing `dist52w` rows with the
  column **median** (document the imputation counts).
- StandardScale all features before fitting (required for Lasso coefficient
  comparability).
- **Time-series cross-validation**: use `sklearn.model_selection.TimeSeriesSplit`
  with `n_splits=5`, splitting on `scan_date` order. Do NOT shuffle — this is
  temporal data and future leakage would inflate the apparent IC.

---

## Step 4 — Model Fitting

### 4a. LassoCV — continuous target

```python
from sklearn.linear_model import LassoCV

model = LassoCV(
    alphas=np.logspace(-4, 2, 60),
    cv=tscv,          # TimeSeriesSplit from Step 3
    max_iter=10_000,
    random_state=42,
)
```

Fit on the full dataset (after time-split CV for alpha selection). Report:
- Selected `alpha`
- Out-of-fold R² (mean ± std across folds)
- Spearman ρ (predicted vs actual) on the held-out fold average

### 4b. Logistic L1 — binary assignment target

```python
from sklearn.linear_model import LogisticRegressionCV

model_bin = LogisticRegressionCV(
    Cs=np.logspace(-3, 3, 40),
    cv=tscv,
    penalty='l1',
    solver='liblinear',
    random_state=42,
)
```

Report:
- Selected C
- Mean out-of-fold ROC-AUC (across folds)
- Brier score on held-out folds

---

## Step 5 — Coefficient / Importance Report

After fitting, produce two ranked tables sorted by **|coefficient|** descending.

### Table format (print + save to CSV)

```
=== LASSO: Top factors predicting realised_roc_annualised ===
  Rank  Feature               Coef    |Coef|   Direction
     1  hv30_pct             +18.4    18.4      Higher HV → higher realised ROC (mech.)
     2  iv_minus_hv          +11.2    11.2      IV premium over HV → seller edge
     3  dist52w_abs          + 6.7     6.7      Far from 52W high → better outcomes (Method D)
     ...
  (zeroed-out features are also listed with Coef=0.00 so all candidates are visible)
```

Include a `Direction` note column explaining the sign in CSP terms.

### Annotation rules for the Direction column

| Sign | Meaning |
|------|---------|
| + on vol feature | More expensive options → higher raw premium yield |
| + on dist52w_abs | Far from 52W high → Method D thesis confirmed |
| − on dist52w_abs | Near high → momentum stocks held up better |
| − on delta_abs | More OTM → less assignment, more cushion |
| + on delta_abs | Closer to ATM → more premium, more risk |
| + on rsi | High RSI → overbought stocks still retained premium |
| − on rsi | Oversold stocks → recovery gave assignment relief |
| + on otm_pct | More cushion → fewer assignments |

Apply domain sense; if a coefficient sign is counter-intuitive, flag it with `⚠️`.

---

## Step 6 — Grouped Validation

For the top-5 nonzero features from the continuous model, produce a **decile
analysis** showing mean `realised_roc_annualised` across decile buckets of that
raw feature. This validates that the Lasso coefficient direction holds across
the distribution, not just at the mean.

Format:
```
=== Decile analysis: hv30 vs realised_roc_annualised ===
  D1  (0.08–0.15)  mean ROC = +11.2%   n=1275
  D2  ...
  ...
  D10 (0.58–0.98)  mean ROC = +24.7%   n=1275
```

---

## Step 7 — Scoring Implication Summary

Print a final section:

```
=== Scoring Implications ===

Factors with |coef| > 3 that are NOT currently in the CSP scorer (Method D):
  [list them — these are candidates for inclusion if they survive OOS validation]

Factors with |coef| ≈ 0 that ARE in the scorer:
  [list them — these are candidates for removal if the pattern holds on the 
   full-v2 dataset]

Factors that Lasso confirms at the expected sign:
  [list — these reinforce the current Method D design]

Factors where Lasso sign contradicts Method D intuition:
  [flag these — they warrant an ADR discussion before any weight change]
```

---

## Step 8 — Save Artefacts

1. `csp_lasso_results.csv` — one row per feature with columns:
   `feature, coef_continuous, coef_binary, abs_coef_continuous, zeroed_out, direction_note`

2. Print a `scripts/csp_lasso_factor_analysis.py` execution summary line at the end:
   ```
   [DONE] alpha=0.0042  OOF R²=0.112±0.018  OOF AUC=0.583  n=18016  saved → csp_lasso_results.csv
   ```

---

## Implementation requirements

- **Python 3.12**, running via `backend\venv\Scripts\python.exe`.
- Script must be executable standalone: `backend\venv\Scripts\python.exe scripts\csp_lasso_factor_analysis.py`.
- Import only from: `pandas`, `numpy`, `scipy`, `sklearn`, `pathlib`, `sys`, `warnings`.
  All of these are already installed in the backend venv.
- Use `pd.read_csv(REPO_ROOT / "csp_backtest_full_v2.csv")`.
- No yfinance calls, no network access — the backtest CSV is the only data source.
- Suppress sklearn convergence warnings with `warnings.filterwarnings('ignore')`.

---

## Validation checklist before finishing

- [ ] Features include both raw inputs AND the current scored components so
  coefficients are directly comparable.
- [ ] No future-leakage: `spot_at_exp`, `pnl_per_contract`, `realised_return_per_dollar`,
  `assigned` are **not** used as features in the continuous model. Only `assigned`
  is used as the binary model target.
- [ ] The script runs in under 60 seconds on the 18k-row dataset.
- [ ] All features are StandardScaled before Lasso (raw dollar amounts like
  `premium` and `spot` would otherwise dominate simply due to magnitude).
- [ ] The output table includes ALL candidate features, not just the nonzero ones.

---

## Expected findings (prior for sanity check)

Based on Spearman ρ computed on this dataset before the Lasso run:

| Feature | Prior ρ | Expected Lasso sign |
|---|---|---|
| `hv30` | +0.666 | ++ (large, mechanical) |
| `iv_pct` | +0.381 | ++ (vol premium) |
| `dist52w_abs` | +0.297 (abs of dist52w) | ++ (Method D) |
| `delta` (abs) | +0.085 | ambiguous (moneyness vs cushion) |
| `rsi` | −0.076 | − (overbought → reversal risk) |
| `sma_ratio` | +0.035 | near zero, may be zeroed out |
| `sma50_slope_pct` | −0.046 | near zero, may be zeroed out |
| `dte` | −0.007 | likely zeroed out |

If `hv30` dominates to the point that it masks everything else, rerun the
analysis **controlling for `hv30`** by including it as a fixed linear term and
running Lasso only on the residuals. Report both the raw and the HV-controlled
coefficient tables.
