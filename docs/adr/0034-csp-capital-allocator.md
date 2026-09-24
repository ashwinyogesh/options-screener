# ADR-0034: CSP Capital Allocator

- **Status**: Accepted
- **Date**: 2026-09-10

## Context

The CSP tab lists individually scored contracts and lets the user filter by a
per-contract collateral cap. But the real decision a CSP seller makes is a
*portfolio* one: "I have $X of cash — what mix of puts should I sell?" Today
the user does this by eye, which biases toward a single large position (e.g. one
$120-strike put) and strands the remainder as idle cash.

Two forces motivate a dedicated allocator:

1. **Capital utilisation.** Fine-grained strikes let a budget be deployed near
   fully instead of leaving a fractional-contract remainder idle. Idle cash is a
   guaranteed yield drag.
2. **Tail-risk diversification.** A CSP payoff is *capped upside* (the premium)
   with a *large left tail* (assignment far below strike). For that payoff shape,
   spreading across low-correlation names cuts return variance roughly like $1/N$
   without lowering expected premium, which raises geometric (compound) growth —
   the variance-drag term $\mu_g \approx \mu - \tfrac{1}{2}\sigma^2$ shrinks.

The benefit is **conditional**, and a naïve "deploy it all" implementation would
destroy it:

- **Correlation.** A basket of four high-beta momentum names (SOFI, UBER, NBIS,
  …) sells off together; realised diversification is far below the $1/N$ ideal.
  Contract *count* is a poor proxy for *risk* spread.
- **Quality dilution.** Forcing full deployment pushes the marginal fill into
  low-score contracts. The v3.4 backtest (ADR-0031) shows scores **< 69 have
  negative mean ROC** (−3.6% to −4.1%). A junk fill added just to use cash lowers
  risk-adjusted return; leaving cash idle beats it.

So the allocator must optimise *risk-adjusted edge*, not raw ROI or contract
count — reward high `csp_score`, cap per-name and per-sector concentration,
enforce a hard minimum-score gate, and permit idle cash.

## Options Considered

1. **Backend service + endpoint** — a `services/csp_allocator.py` with a new
   route. Pros: testable in pytest, reusable by the precompute job, follows
   layering. Cons: another network round-trip; the allocator only needs data the
   client already holds; adds surface area for a UI-driven "what-if" knob that
   users will slide rapidly.
2. **Frontend-only pure module** — compute in a hook from already-fetched
   results. Pros: zero latency for slider/cap changes, no backend load, no new
   API contract, no external data. Cons: sector map must live client-side; math
   is TS not pytest (mitigated by vitest).
3. **Exact integer optimiser (ILP/branch-and-bound)** — provably optimal knapsack
   fill. Pros: optimal. Cons: NP-hard, opaque, hard to explain per-pick, overkill
   for a screening aid where inputs are noisy estimates.

## Decision

Ship a **frontend-only, greedy value-density allocator** (Option 2 + a greedy
core rather than Option 3).

- **Objective.** Per eligible contract, blend normalised safety and ROI with a
  single risk-tolerance knob $w \in [0,1]$:
  $\text{value} = (1-w)\cdot\widehat{\text{score}} + w\cdot\widehat{\text{ROC}}$,
  where **both** $\widehat{\text{score}}$ and $\widehat{\text{ROC}}$ are min–max
  normalised across the eligible set. $w=0$ ranks on safety, $w=1$ on yield.
  Symmetric scaling is required: a fixed `csp_score/100` safety scale, combined
  with the 69 gate compressing scores into ~[0.69, 1.0], gives full-range min–max
  ROI ~3× the leverage, pinning the slider on the aggressive pick across most of
  its travel (observed live 2026-09-10). Normalising both axes makes the slider
  sweep smoothly end to end.
- **Hard safety floor.** A minimum `csp_score` gate (default **69**, the v3.4
  "take it" cliff) filters candidates *before* ranking, so even full-aggressive
  never buys a negative-edge contract.
- **Concentration caps.** Per-name (default 35% of capital) and per-sector
  (default 50%) dollar caps, enforced during a greedy fill. Sectors come from a
  static client map sourced from the curated universe groupings.
- **Integer, idle-cash-tolerant fill.** One best contract per name; buy the max
  contracts each cap allows, descending by value; stop when nothing fits. Idle
  cash is surfaced, never disguised with a sub-gate fill.
- **Concentrated baseline.** The result includes a one-name "put it all in the
  top pick" comparison so the user can see the diversification trade-off directly.

Greedy-with-caps is near-optimal for this bounded-knapsack shape and, decisively,
**explainable** — each pick's contract count is a transparent function of the
three caps.

## Consequences

- **Positive.** No backend change or new API; slider/cap edits recompute
  instantly. The min-score gate reuses the empirically validated v3.4 cliff, so
  the allocator inherits the backtested edge. Per-pick reasoning is inspectable.
  Pure module is unit-tested (`cspAllocator.test.ts`).
- **Negative.** The sector map is a second, client-side ticker grouping that must
  be kept roughly in sync with `backend/services/universe.py` (it is coarse and
  assignment-risk-oriented, not GICS-exact, which limits drift damage). The
  allocator does not model cross-name correlation or beta — sector caps are a
  proxy, so a basket inside one high-beta sector can still be more correlated than
  it looks. Greedy is not provably optimal.
- **Neutral.** The allocator is a *sizing aid*, not a recommendation engine; it
  operates only on contracts already on screen and after active filters.

## Update — 2026-09-10: concentration caps removed

The per-name (35%) and per-sector (50%) caps described under **Decision** were
**removed** the same day, at the user's direction, after live use showed they
preferred running with the caps disabled (set to 100%). The allocator is now
bounded only by the capital ceiling and the min-score gate; the top-ranked
contract fills to the capital limit before the next is considered, so a single
name can take an arbitrary share of the book.

- **What stays:** the min-score gate (the actual safety floor), idle-cash
  tolerance, the sector map, and the `sectorBreakdown` / `largestNamePct` metrics
  — now purely *informational* rather than *enforced*.
- **Consequence:** diversification is emergent from ranking + collateral
  granularity, not guaranteed. The concentrated-baseline comparison and sector-mix
  display remain so the user can judge concentration manually.
- **Reversibility:** the caps were a self-contained block in the greedy loop and
  two `AllocatorSettings` fields; re-introducing them is a small change if a
  future need arises.

## Update — 2026-09-10: EM-floor fill mode trialed and removed

An EM-floor fill mode was prototyped the same day: a second selection strategy
that kept only strikes at/below the bottom of the expected move
(`emFraction = (spot − strike) / expected_move`), gated on `env_score`, and
ranked by premium yield — a capital-preservation lens.

It was **removed the same day** as not worth its weight. In practice the deep,
low-premium strikes it favoured, combined with a small on-screen candidate set,
produced thin or empty baskets, and the mode did not earn its added surface area
(a mode toggle, two extra settings, and per-pick EM columns) over the simpler
**Best edge** default. The allocator is back to a single edge-ranked path.

- **Removed:** the `mode` / `minEnvScore` / `emFloorMin` settings, the
  `emFraction` / `envScore` fields, the `weightedEmFraction` / `weightedEnvScore`
  metrics, and the mode UI. The edge path (caps-free, symmetric-normalised blend)
  is unchanged.
- **If revisited:** the EM-fraction math and the "gate on `env_score`, not the
  composite" reasoning are preserved in this record — the composite double-counts
  strike depth, so a name-quality gate is the right one for a distance-based mode.

## Update — 2026-09-10: Optimized (knapsack) mode added

Added a second, opt-in **Optimized** mode alongside greedy **Best edge**. It runs
a multiple-choice bounded-knapsack DP over *every* eligible (name, strike)
contract, jointly choosing the strike per name (≤ 1 strike per name, any count) to
maximise total merit subject to the capital budget.

- **Why:** greedy pre-fixes one strike per name before filling, so it can't
  consider "use CRWV $70 instead of $75 to build a better whole-portfolio fit."
  Making the strike a decision variable is the real value — not greedy-vs-exact.
- **Safety proxy (this exercise):** distance-to-spot (`otm_pct`) — a deeper strike
  is safer. Per-contract merit blends min-max-normalised distance and ROC via the
  existing slider. Diversification is explicitly deferred to a later pass.
- **Objective:** maximise `Σ merit·contracts` s.t. `Σ collateral ≤ capital`,
  integer contracts. Solved exactly by DP; capital discretised into units (scaled
  up for large budgets) to bound the table.
- **Honest limits:** maximises a *merit proxy*, not dollars; inputs are noisy, so
  exactness is only as good as the estimates; it does **not** enforce
  diversification, so one name can still dominate.
- **Scope:** pure client-side; adds a `mode` setting, `otmPct` on picks, a
  `weightedOtmPct` metric, and the knapsack DP. Unit-tested in `cspAllocator.test.ts`.

## Update — 2026-09-10: Optimized objective → dollar-weighted + per-name cap

Live use of the Optimized mode surfaced a degenerate result: 12× SOFI $15 = 100%
of capital in one low-score name. Two root causes, both fixed:

- **Cheap-stock bias.** The count-weighted objective $\sum \text{merit}\cdot n$
  reduces to maximising merit *per dollar of collateral*, so the cheapest decent
  strike always wins on contract count. Switched to **dollar-weighted merit**
  $\sum \text{merit}\cdot\text{deployed}$ (per-contract objective weight =
  `merit × collateral`), which ranks each *dollar* by quality — equivalent to
  maximising the capital-weighted average distance/ROC when the budget is filled.
- **No diversification.** Any additive distance/ROC objective concentrates in one
  name without a constraint. Re-introduced a **per-name cap** (`maxNamePct`,
  default 35%) as a DP constraint on the per-name contract count.

Both are Optimized-mode only; Best edge is unchanged. Unit-tested (cap enforced,
and expensive-high-merit name beats cheap ones). Note: the cap limits *dollars*,
not correlation — a genuine correlation/beta model remains a follow-up.

## Update — 2026-09-11: Optimized safety axis → delta (assignment probability)

A CSP-trader review flagged that the Optimized mode's safety axis (`otm_pct`,
raw distance-to-spot) is **volatility-blind**: 10% OTM is ~5-delta on a low-vol
name and ~35-delta on a high-vol one, yet distance ranks them equally safe.
Switched the safety component to **|put delta|** (≈ assignment probability),
normalised as `1 − |delta|` so lower delta = safer. Delta is already in the
payload and is IV-aware, so it ranks cross-name safety correctly.

- **Scope:** Optimized mode only; the merit blend now trades |delta| against ROC.
  Adds `absDelta` to candidates/picks and a `weightedDelta` metric (replaces the
  distance metric/column with Δ). Unit-tested.
- **Still open (from the same review):** the *return* axis is still gross ROC, not
  EV net of assignment; annualisation flatters short-dated high-IV; the per-name
  cap limits dollars not correlation; and full-deployment ignores dry-powder value.
  Deltas addressed the highest-leverage item only.

## Update — 2026-09-11: Optimized return axis → vol-normalised yield

Same CSP-trader review: raw annualized ROC overstates edge on high-IV names (fat
premium is largely payment for vol, and that's where assignment bites). Switched
the Optimized return axis from ROC to **yield efficiency** =
`ROC / expected-move%` — ROC earned per 1% of the stock's own expected move.

- **Why not premium-per-delta or EV.** `ROC/|delta|` double-counts the delta
  safety axis (both ends of the slider would lean low-delta, collapsing the dial).
  EV net of assignment cancels to ~0 under fair pricing (`premium ≈ |delta|×E[loss]`),
  giving no ranking signal. Vol-normalisation is orthogonal to delta (magnitude vs
  probability), so the slider stays meaningful.
- **Scope:** Optimized mode only; merit now blends `1−|delta|` (safety) against
  `yieldEff` (return). Adds `yieldEff` to candidates/picks and a `weightedYieldEff`
  metric + column; raw ROC column retained. Unit-tested (same-ROC low-vol name wins).
- **Still open:** annualisation still flatters short-dated; the cap limits dollars
  not correlation; full-deployment ignores dry-powder value.

## Update — 2026-09-11: Concave merit (soft diversification) added

Live use showed the linear objective + hard cap has a blunt failure: when one name
is genuinely much better, removing the cap dumps 100% into it, and keeping the cap
*forces* capital into clearly-worse names just to obey the % line (quality-blind).

Added an optional **concave merit** (`diversification`, 0–1, default 0 = off): the
j-th contract of a name is worth `d^(j-1)` of the base (geometric decay, `d = 1 −
0.6·diversification`), summed via a closed form. This makes the book spread *in
proportion to the quality gap* — it keeps more in a dominant name and only diversifies
when marginal merit justifies it, instead of the cap's hard cliff.

- **Cap vs concave:** kept both. Cap is the *hard* ceiling (guarantees no name > X%);
  concave is the *soft, quality-aware* nudge. Used together: concave spreads smartly,
  cap backstops the extreme.
- **Scope:** Optimized mode only; slots into the existing per-name (strike, count)
  DP options by weighting each count's merit with the concave sum. Unit-tested
  (concave basket spreads wider than linear).
- **Limit:** concave only curbs *single-name* concentration, not correlation — the
  mean-variance rung above still owns that.
- **Default (2026-09-13): 0.15**, not off. Rationale: within-noise merit
  differences make concentrating in whichever name edged ahead false precision;
  a mild concave default spreads across effectively-tied names (breaks ties within
  ~10% merit) while still respecting genuinely-better names and keeping the cap as
  the hard backstop. Higher values (which spread into clearly-worse names) stay opt-in.

## Update — 2026-09-17: Happy Price to Own (HPO, v1)

Added a passive **Happy Price to Own** reference per pick — an objective anchor for
the "would I be happy to own the shares here?" decision, replacing the gut call
with a support-confluence number.

- **v1 (pure client-side):** confluence of volume-support levels (below spot) + the
  expected-move floor → a point (median), a zone (min–max), and a spread% (tightness
  = agreement = confidence). Break-even badge (🟢/🟡/🔴) vs the zone.
- **Downtrend guard:** HPO stays silent in a confirmed downtrend rather than print a
  falling-knife number — the most important design decision (a tool that knows when
  not to answer is more trustworthy).
- **Passive by design:** display + badge only; deliberately **not** wired into merit
  until it's proven itself against live picks.
- **Deferred:** 200-DMA anchor + relative-value (valuation) leg — the latter is what
  lets HPO exceed spot for "cheap vs its own history" names; both need backend data.
- Unit-tested in `happyPrice.test.ts`.

## Update — 2026-09-17: HPO v2 — independent-family confluence

Reworked HPO from a flat anchor spread into **agreement across independent families**,
after realising v1 over-counted the same signal: three volume-support nodes look like
"confluence" but are one volume profile, inflating confidence for a single family.

- **Backend (new, additive fields on `CspResult`):** `sma_200` (200-DMA, trend),
  `avwap_52w_low` (anchored VWAP from the 52-week low, institutional cost basis), and
  `put_wall` (largest scored put OI below spot, options positioning). All default-valued
  and threaded through `SymbolMetrics` → router `CspResultOut` → the result store; the
  golden characterization fixtures were regenerated (additive-only diff).
  - *`put_wall` limitation:* computed from the scored (delta-band) strikes rather than
    the full put chain — a v1 proxy that avoids threading the whole chain through the
    runner. A deeper round-number wall outside the band can be missed.
- **Families (independent votes):** structure (volume-support nodes, kept at their
  real price levels), options (put wall), trend (200-DMA), institutional (52w-low
  AVWAP), volatility (EM floor). The volume-support nodes stay as actual levels but
  count as a **single** independent family — Value-Area/POC would be redundant with
  them, and collapsing them to a synthetic median printed a "happy price" that matched
  no visible support level (the HOOD $84 bug). The cluster scorer counts distinct
  families, so several nodes of one profile can't inflate confidence.
- **Confidence = independent agreement:** a ±6 % mode-seeking cluster picks the densest
  group of families; **≥3 agree → high, 2 → medium**, <2 → silent. This replaces the
  old flat `spread%` bands, which could read "high" off one family's tight internal spread.
- **Near-spot filter (new guardrail):** anchors within ~5 % of spot are dropped — a
  near-spot level isn't a "discount to own" (and this also removes above-spot anchors).
- **Downtrend silence and passive-by-design are unchanged** — still display + badge only,
  not in merit. Tooltip now lists each family and marks the consensus members (✓).
- Re-validated on live data: NBIS resolves to a **high-confidence ~$184** zone (vol
  support $185 + put wall ~$180 + 52w-low AVWAP $184.83 — three independent families),
  versus v1's low-confidence ~$195 median that was dominated by near-spot vol nodes.
- Tests updated in `happyPrice.test.ts` (family agreement, near-spot filter, tagged
  anchors); CSP characterization fixtures regenerated.

## Update — 2026-09-18: Optimized-mode audit fixes (#2 return-axis, #3 delta guard)

A CSP-trader audit of the Optimized tab surfaced a horizon bug and a robustness gap;
this update addresses those two ranking-behaviour items (the audit's #2 and #3a). The
three hard gates it also flagged — earnings-in-DTE, liquidity, stale/fallback flags —
are **not** yet done and remain the top open follow-up.

- **Return-axis horizon fix.** `yieldEfficiency` divided *annualized* ROC by a
  *single-DTE* expected move, inflating short-dated/high-churn strikes in the ranking.
  Now it uses **per-trade** ROC (`premium ÷ strike`) over the same-period EM% — both on
  one clock. Since the axis is min-max normalised, same-DTE sets are unaffected; the fix
  only removes the cross-DTE short-dated bias. Display precision bumped to 2 dp (the
  metric's scale is smaller now).
- **Degenerate-delta guard.** Strikes with `|delta| = 0` (feed miss / failed compute)
  are dropped in `buildOptimizedCandidates`, so they can no longer normalise to maximum
  safety and steal a slot. (The edge builder has the same latent case; left as-is since
  the audit scoped the optimizer.)
- **Considered and rejected: an opt-in HPO break-even tie-break.** A ±10% merit nudge
  toward 🟢 (below-zone) break-evens was prototyped, then removed — Happy Price stays
  strictly a *passive* display signal, not a selection input, keeping merit a clean
  safety×yield function and the two axes independent.
- Tests: added a delta-guard exclusion case; full suite green (build clean). Backend
  untouched.

## Follow-ups

- [ ] **Add the three hard gates** the audit flagged (earnings-in-DTE, liquidity
      spread/OI, stale/fallback flags) to `buildOptimizedCandidates` — highest
      trader-impact remaining item.
- [ ] If correlation matters in practice, add a beta/vol-aware penalty (needs
      per-name beta on the client or a backend enrichment) — deferred as a heavier
      lift.
- [ ] Revisit whether the allocator should move server-side if the precompute job
      ever wants to publish a "suggested basket" per universe.
- [ ] Keep `frontend/src/constants/sectors.ts` aligned when the universe changes.
