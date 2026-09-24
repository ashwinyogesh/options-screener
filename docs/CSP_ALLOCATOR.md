# CSP Capital Allocator

Given a capital budget and the CSP contracts currently on screen, the allocator
proposes an integer basket of cash-secured puts that best fills the budget. Two
modes: **Best edge** (greedy, ranks each name's best strike by a risk-adjusted
blend) and **Optimized** (a bounded-knapsack DP that jointly chooses the strike
per name to maximise a distance-to-spot / ROC blend). It is a pure client-side
sizing aid (no network, no new scoring constants); the decision record is
[ADR-0034](adr/0034-csp-capital-allocator.md).

## Section index

- [Why split capital across contracts?](#why-split-capital-across-contracts)
- [When splitting does *not* help](#when-splitting-does-not-help)
- [Inputs and eligibility](#inputs-and-eligibility)
- [Objective function](#objective-function)
- [Allocation algorithm](#allocation-algorithm)
- [Constraints](#constraints)
- [Optimized mode (knapsack)](#optimized-mode-knapsack)
- [Output metrics](#output-metrics)
- [The concentrated-baseline comparison](#the-concentrated-baseline-comparison)
- [Limitations](#limitations)

## Why split capital across contracts?

A cash-secured put has a **capped upside** (the premium credit) and a **large
left tail** (assignment far below strike). For that payoff shape, spreading the
same capital across low-correlation names reduces the variance of portfolio
return roughly like $1/N$ without lowering expected premium. Lower variance on a
capped-upside bet raises **geometric (compound) growth**, because the
variance-drag term shrinks:

$$\mu_{g} \approx \mu - \tfrac{1}{2}\sigma^{2}$$

There are two independent wins:

- **Tail isolation.** One name gapping down assigns one sleeve, not the whole book.
- **Capital utilisation.** Fine-grained strikes deploy the budget more fully;
  idle cash is a guaranteed yield drag.

## When splitting does *not* help

The diversification benefit is **conditional**. A naïve "deploy it all"
allocator would forfeit it:

- **Correlation.** Four high-beta momentum names (SOFI, UBER, NBIS, …) sell off
  together — realised diversification is far below the $1/N$ ideal. Contract
  *count* is a poor proxy for *risk* spread; sector/beta exposure is what matters.
- **Quality dilution.** Forcing full deployment pushes the marginal fill into
  low-score contracts. Per the v3.4 backtest
  ([SCORING_REFERENCE.md](../SCORING_REFERENCE.md),
  [ADR-0031](adr/0031-csp-scoring-empirical-validation.md)), scores **below 69
  carry negative mean ROC** (−3.6% to −4.1%). Adding a junk contract just to use
  cash *lowers* risk-adjusted return — idle cash beats a sub-gate fill.
- **Cheap ≠ safe.** Low-priced stocks often carry higher IV, assignment
  probability, and deeper percentage drawdowns; small collateral hides real risk.
- **Frictions.** More legs mean more spread slippage and roll/management overhead.

The allocator's design answers two of these directly: a hard score gate (quality
dilution) and idle-cash tolerance (never a junk fill). It does **not** enforce
per-name or per-sector caps — correlation management is left to you. Use the
sector-mix breakdown and the concentrated-baseline comparison to judge whether
the basket is genuinely spread or just several names in one theme.

## Inputs and eligibility

The allocator consumes the CSP results **already on screen, after active
filters** — no fresh fetch. Every `(symbol, expiration, strike)` becomes a
candidate. A candidate is **eligible** only if:

- `premium > 0`, and
- `csp_score ≥ minScore` (the safety gate), and
- `collateral = strike × 100 ≤ capital`.

Only the single best-value contract **per symbol** is kept — stacking multiple
strikes of the same stock is concentration, not diversification.

## Objective function

Each eligible candidate gets a blended value in $[0,1]$ controlled by a single
risk-tolerance knob $w \in [0,1]$ (the Conservative ↔ Aggressive slider):

$$\text{value} = (1-w)\cdot\widehat{\text{score}} \;+\; w\cdot\widehat{\text{ROC}}$$

where **both** axes are min–max normalised across the eligible set so that equal
weight gives equal leverage:

$$\widehat{\text{score}}_i = \frac{\text{score}_i - \min_j \text{score}_j}{\max_j \text{score}_j - \min_j \text{score}_j}, \qquad \widehat{\text{ROC}}_i = \frac{\text{ROC}_i - \min_j \text{ROC}_j}{\max_j \text{ROC}_j - \min_j \text{ROC}_j}$$

(ROC is `roc_annualized`, falling back to `annualized_return`; a degenerate zero
span maps to 0.5.) Normalising *both* sides is deliberate: a fixed `csp_score/100`
scale would, after the 69 gate compresses scores into ~[0.69, 1.0], give the
full-range min–max ROI roughly 3× the leverage — so the slider would sit on the
aggressive pick across most of its travel. With symmetric scaling the winner
sweeps smoothly from the highest-score contract at $w=0$ to the highest-ROC
contract at $w=1$.

At $w=0$ contracts are ranked purely on safety (`csp_score`); at $w=1$ purely on
yield. The **min-score gate is applied before ranking**, so the safety floor
holds even at full-aggressive — the slider only tilts *within* the gated set.

## Allocation algorithm

A greedy value-density fill (near-optimal for this bounded-knapsack shape, and
per-pick explainable):

1. Sort eligible candidates by `value` desc → `csp_score` desc → cheaper
   collateral first.
2. Walk the sorted list. For each candidate buy `n = ⌊remaining capital /
   collateral⌋` contracts (skip if `n < 1`).
3. Decrement remaining capital; continue.
4. Stop when no remaining candidate fits. Leftover capital is reported as **idle
   cash** — the allocator never relaxes the gate to force a fill.

The top-ranked contract fills to the capital limit before the next is
considered, so a single high-value name can take a large share of the book;
spread across names is emergent from ranking and collateral granularity, not an
enforced cap.

## Constraints

| Constraint | Default | Effect |
|-----|--------:|--------|
| Min `csp_score` gate | 69 | Hard eligibility floor — the v3.4 "take it" cliff. Contracts below it are never used, even to deploy idle cash. |
| Capital ceiling | your budget | Total deployed collateral never exceeds it; the remainder is surfaced as idle cash. |

The min-score gate is user-adjustable in the panel. Per-name and per-sector
concentration caps were part of the original design but were removed
(2026-09-10, see [ADR-0034](adr/0034-csp-capital-allocator.md)); the allocator
no longer limits how much capital a single name or sector can take.

## Optimized mode (knapsack)

The default **Best edge** mode is greedy and pre-fixes one strike per name before
filling. **Optimized** mode instead treats the strike itself as a decision
variable: it runs a bounded-knapsack DP over *every* eligible (name, strike)
contract and jointly chooses the strike per name that best fills the budget.

- **Return proxy (this mode).** Not raw ROC — **vol-normalised yield**:
  $\text{yieldEff} = \text{ROC} / \text{EM\%}$, the **per-trade** return on collateral
  earned per 1% of the stock's own expected move, both measured over the *same*
  holding period. (Earlier it annualised only the ROC numerator against a
  single-horizon expected move, which structurally flattered short-dated strikes —
  fixed per the ADR-0034 audit.) Raw ROC overstates edge on high-IV names (fat premium
  is mostly you being paid for volatility, and that's exactly where assignment
  bites); dividing by the expected move strips that illusion so premium is only
  rewarded when it's rich *relative to the name's vol*. Per contract, a **merit**
  blends min-max-normalised safety (`1 − |delta|`) and yieldEff via the risk slider:
  $\text{merit} = (1-w)\,\widehat{\text{safety}} + w\,\widehat{\text{yieldEff}}$. At
  $w=0$ it favours the lowest-delta strikes; at $w=1$ the most vol-efficient yield.
  (Safety = *probability* of assignment, yieldEff = *richness per unit of risk* —
  deliberately different dimensions so the slider stays meaningful.) Strikes with no
  usable delta (`|delta| = 0`) are **dropped**, so a failed-to-compute delta can't
  normalise to maximum safety and win a slot.
- **Objective.** Maximise **dollar-weighted merit** $\sum_i \text{merit}_i \cdot \text{deployed}_i$
  subject to $\sum \text{collateral} \le \text{capital}$, integer contracts, **at most one
  strike per name**, and **≤ `maxNamePct` of capital per name** (multiple-choice
  bounded knapsack, solved exactly by DP; capital discretised to bound the table).
  Weighting merit by *dollars deployed* rather than *contract count* is deliberate:
  a count-weighted sum $\sum \text{merit}_i \cdot n_i$ reduces to maximising merit
  *per dollar of collateral*, which structurally favours cheap strikes (a $1,500
  name racks up more contracts per dollar than a $17,000 one). Dollar-weighting
  ranks each *dollar* by quality instead — which is exactly what maximising the
  capital-weighted average distance/ROC requires.
- **Diversification.** The per-name cap (default 35%) keeps the book spread; without
  it, any additive distance/ROC objective degenerates to 100% in a single name.
  An optional **concave merit** ("Diversify", default off) makes each additional
  contract of the same name worth less — a *soft* spread that respects the quality
  gap (it keeps more in a genuinely-better name, unlike the cap which forces capital
  into worse names to obey its line). Cap = hard backstop; concave = smart nudge.
- **Why it can beat greedy.** Because the strike is a free variable, it may pick a
  *lower* strike than the top-ranked one (e.g. $70 instead of $80) when that frees
  capital or improves the whole-portfolio merit — exactly the combination greedy
  can't see with its pre-fixed per-name strike.
- **Honest limits.** It maximises a *merit proxy*, not dollars of value, and the
  inputs are noisy (yfinance mids, HV-based fields), so the exact optimum is only
  as meaningful as the estimates. The per-name cap is a crude diversification proxy
  — it caps dollars, not correlation.

## Output metrics

The basket reports, per pick: contracts, collateral, `% of capital`, premium
credit, `csp_score`, annualized ROC, and expiry. Portfolio-level:

- **Deployed / idle / utilisation** — dollars committed vs left uncommitted.
- **Basket size** — number of names and total contracts.
- **Premium credit** — total up-front income.
- **Weighted score** — capital-weighted mean `csp_score`.
- **ROC on deployed** — capital-weighted annualized ROC on committed collateral.
- **ROC on capital** — the above scaled by utilisation, i.e. including idle-cash drag.
- **Avg Δ** (Optimized mode) — capital-weighted mean `|put delta|`, the portfolio's
  blended assignment-probability (safety) proxy.
- **Yield eff** (Optimized mode) — capital-weighted vol-normalised yield (per-trade
  ROC per 1% of expected move); higher = richer premium relative to the names' own vol.
- **Happy Price to Own** — an objective "price I'd be happy to own the shares at,"
  from the confluence of support anchors (see below).
- **Top-name %** and a **sector mix** breakdown.

### Happy Price to Own (HPO, v2)

Objectifies the cornerstone CSP question — *"would I be happy to own the shares at
this price?"* — so it's a number, not a gut call. v2 groups anchors into **independent
families** and measures agreement *across* families: three unrelated signals pointing
at the same level is real confluence; three flavours of the same volume profile is not.

- **Families (each contributes its real price level(s); structure counts once):**
  - *structure* — volume-support nodes kept at their **actual price levels** (several
    nodes are one volume-profile family, so they count as a single independent vote —
    no synthetic median),
  - *options* — the **put wall** (largest scored put OI below spot),
  - *trend* — the **200-day moving average** (`sma_200`),
  - *institutional* — **anchored VWAP from the 52-week low** (`avwap_52w_low`), the
    average cost basis of buyers since the bottom,
  - *volatility* — the lower **expected-move floor** (`spot − expected_move`).
- **Consensus:** a mode-seeking cluster finds the group of anchors under a ±6 %
  tolerance that spans the most **independent families**. The **point** is the median
  of the agreeing anchors, the **zone** is their min–max, and **confidence** = how many
  independent families agree (**≥3 → high, 2 → medium**). Fewer than two agreeing
  families → HPO stays silent (no real confluence).
- **Guardrail 1 — downtrend silence:** HPO goes **silent** (“⚠ dt”) when the stock is
  in a confirmed downtrend (`SMA50 < SMA200`, below the 20-day mean, RSI < 45) —
  support anchors trail a falling price, so a “happy price” there is a knife, not a floor.
- **Guardrail 2 — near-spot filter:** an anchor within ~5 % of spot isn't a discount to
  own; only anchors at least that far below spot qualify (this also drops any anchor
  sitting above spot).
- **Break-even badge:** compares each pick's break-even (`strike − premium`) to the
  zone — 🟢 below the whole zone, 🟡 inside it, 🔴 above it (owning above where buyers
  defended). Passive reference only — **not** wired into the optimizer's ranking. The
  cell tooltip lists every contributing family (✓ = in the consensus cluster).
- **Deferred:** a relative-value (valuation) leg — the only path to a happy price
  *above* spot — still needs fundamentals.

## The concentrated-baseline comparison

To answer "is diversifying actually worth it?" in-line, the result includes a
**concentrated baseline**: the whole budget placed in the single top-ranked name
(as many contracts as fit). The panel contrasts its premium and 100%-single-name
concentration against the actual basket's income and largest single-name share.
The variance reduction — not a higher headline yield — is the edge, and it only
holds when the names are not all one sector, so the sector-mix breakdown is the
check to run before trusting it.

## Limitations

- **No concentration caps.** Nothing limits per-name or per-sector exposure, so
  the top-ranked contract can dominate the book. Diversification is emergent, not
  enforced — read the sector mix and top-name % yourself.
- **No explicit correlation/beta model.** Even the sector breakdown is coarse; a
  basket inside one high-beta sector can be more correlated than it looks.
- **Greedy, not provably optimal.** Adequate and transparent for a noisy-input
  screening aid; an exact ILP was rejected as opaque overkill (ADR-0034).
- **Sizing aid, not a recommendation.** It operates only on contracts already on
  screen and inherits their scoring caveats.
- **Client-side sector map.** `frontend/src/constants/sectors.ts` must be kept
  roughly aligned with `backend/services/universe.py` when the universe changes.
