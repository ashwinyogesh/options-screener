# DD Coach — Methodology (V2)

> Plain-English due-diligence wizard for non-finance retail investors.
> This doc and the code in `backend/services/dd_coach/` change together.
> If the math here disagrees with the services, the code wins and this doc
> needs a PR.

## Mission

Walk a non-finance retail investor through 10 short screens that produce a
written investment thesis, a target-realism check, and a pre-committed
position plan — without ever showing finance vocabulary or asking them to
pick a model.

## Scope (V2)

10 sequential screens, in this order:

1. **The Business** — Data Card + Hard Rails. If the card flags red items, the
   user must explicitly react before continuing (`answers.q1_flag_response`).
2. **What They Sell** — short text on revenue model & concentration.
3. **The Market** — TAM / share / growth.
4. **The Moat** — one-liner (network / brand / switching / cost / IP).
5. **Leadership** — CEO identity, insider activity, comp structure, concerns
   (`answers.q5_leadership`). Encourages cross-reference to Proxy + Form 4.
6. **Path to Target** — user picks a target price; we show three concrete paths
   to get there (growth-only, multiple-only, mixed) with realism bands.
7. **The Risks** — top 3 from 10-K Risk Factors with a 30-minute skim guide.
8. **Why Now** — catalyst within 12 months.
9. **Bear Case** — steel-manned 50%-loss scenario (`answers.q9_bear_case`,
   minimum 30 chars).
10. **Decision & Plan** — call + size + stomach test, then a pre-committed
    sell target, optional add-more price, bail-out trigger (≥20 chars),
    and an explicit commitment acknowledgment (`sizing.commitment_*`).

The frontend wizard renders these. The backend exposes:

| Endpoint | Purpose |
|---|---|
| `GET /api/dd_coach/data_card/{ticker}` | Screen 1 facts |
| `GET /api/dd_coach/filings/{ticker}` | SEC EDGAR landing-page links |
| `GET /api/dd_coach/path_to_target/{ticker}?target_price=` | Screen 6 paths |
| `POST /api/dd_coach/valuation` | Optional fair-value compute (kept for V1 callers) |
| `POST/GET/PATCH /api/dd_coach/entries` | CRUD on the saved thesis |

## Hard Rails (exactly two, non-blocking)

Implemented in [`data_card_service._compute_hard_rails`](../backend/services/dd_coach/data_card_service.py). A red banner appears on Screen 1 if **any** trigger fires; the user can proceed.

| Rule | Trigger | Message rendered |
|---|---|---|
| 1. Persistent cash burn | All three most recent annual FCF values < 0 | "Negative free cash flow 3 years running." |
| 2. Runway risk | `cash / avg_annual_burn < 1.0` (less than 12 months) | "Less than 12 months of cash runway at current burn." |
| 3. Leverage | `total_debt / ebitda > 4.0` | "Debt is X.Xx annual operating profit (high)." |

`avg_annual_burn` is the mean of the absolute values of negative-FCF years
within the 3-year window. `ebitda` falls back to `Operating Income` when
`info.ebitda` is missing.

The DD Coach plan calls these "two hard rails" because Rules 1 and 2 share a
single banner ("balance sheet looks fragile") in the UI; Rule 3 is the second
banner ("debt load looks heavy"). The backend reports them as a flat
`reasons[]` list so the frontend controls the grouping.

## Growth Lens (conditional)

Shown by `data_card_service._build_growth_lens` only when the **latest** FCF
year is negative. Three numbers + one English summary:

- **Gross margin (3-year series)** — `gross_profit / revenue` per year.
- **Cash runway (years)** — `cash / avg_annual_burn`.
- **Share dilution (% over 3 years)** — `(shares_latest - shares_earliest) / shares_earliest` on diluted average shares.

Summary text composition rules:

- If gross margin improved by >2 pts → "Gross margins are expanding as the business scales."
- If gross margin worsened by >2 pts → "Gross margins are deteriorating — unit economics are not yet improving."
- If runway ≥ 3y → "~Xy of cash before raising again."
- If 1y ≤ runway < 3y → "Cash runway is only ~Xy — dilution risk is real."
- If runway < 1y → "Cash runway is under a year — near-term dilution or financing is almost certain."
- If dilution > 10% → "Share count grew X% — your slice of the pie shrank by that much."

## Valuation Auto-Selector (Q5)

The user never picks a model. [`valuation_service.select_method`](../backend/services/dd_coach/valuation_service.py) picks one of three based on the company's profile:

| Profile | Trigger (in code) | Method |
|---|---|---|
| Profitable compounder | All 3 most recent FCF values > 0 | **Multiple-Based** |
| Growing but unprofitable | Latest revenue > $50M **and** gross margin improving | **Maturity-Discount** |
| Pre-revenue / speculative | Everything else | **Optionality** |

The UI surfaces the choice as a one-line rationale (e.g. "We're valuing this
like a mature business because it's been cash-flow positive for 3+ years")
without using the words "DCF", "multiple", or "P/E".

## Method Math

All three methods return a `ValuationOutput(method, range, inputs_used, rationale)`. `range` is `(bear, base, bull, spot)` per-share.

### Multiple-Based

For a stable profitable business. Inputs: `forward_eps`, three target P/E points (low / mid / high), optional `spot_price`.

$$
\text{per\_share}_k = \text{forward\_eps} \times \text{target\_pe}_k
\quad k \in \{\text{low}, \text{mid}, \text{high}\}
$$

Sector P/E defaults (used if user doesn't override; tagged "approximate"):

| Sector | Bear | Base | Bull |
|---|---|---|---|
| cloud-infra | 18 | 28 | 38 |
| semis-ai-compute | 15 | 25 | 40 |
| quantum-speculative | 25 | 50 | 80 |

### Maturity-Discount

For growing-but-unprofitable. The investor imagines the company "grown up" in N years, applies a mature-business sales multiple, then discounts back. Inputs: three revenue scenarios at year T, mature sales multiple, today's diluted shares, dilution %, years to maturity, discount rate.

$$
\text{future\_shares} = \text{shares\_today} \times (1 + \text{dilution\_pct})
$$

$$
\text{discount} = (1 + r)^{T}
$$

$$
\text{per\_share}_k = \frac{R_{T,k} \times M}{\text{future\_shares} \times \text{discount}}
\quad k \in \{\text{bear}, \text{base}, \text{bull}\}
$$

Defaults: $T = 4$ years, dilution = 30%, $r = 12\%$.

Sector P/S defaults for $M$:

| Sector | Bear | Base | Bull |
|---|---|---|---|
| cloud-infra | 6 | 10 | 14 |
| semis-ai-compute | 8 | 14 | 20 |
| quantum-speculative | 10 | 20 | 35 |

**Worked example (NBIS-style):** revenue at maturity 1.5B / 3.0B / 5.0B, multiple 10x, 410M shares today, 30% dilution, 4 years, 12% rate.

- `future_shares = 410M × 1.30 = 533M`
- `discount = 1.12⁴ ≈ 1.5735`
- Bear: `(1.5B × 10) / 533M / 1.5735 ≈ $17.9`
- Base: `(3.0B × 10) / 533M / 1.5735 ≈ $35.8`
- Bull: `(5.0B × 10) / 533M / 1.5735 ≈ $59.6`

### Optionality

For pre-revenue speculatives where any DCF is fiction. The service refuses to fabricate a range and returns `(None, None, None, spot)`. The UI is expected to render: "We can't put a number on this. Treat the position as an option premium — only invest what you would lose at a poker table."

## Sector defaults — source of truth

[`valuation_service.SECTOR_MULTIPLES_PSALES`](../backend/services/dd_coach/valuation_service.py) and `SECTOR_MULTIPLES_PE`. V1 ships with three sectors only (cloud-infra / semis-ai-compute / quantum-speculative). The frontend reads these via the locked DD Coach plan; adding a sector requires updating both the constant and this table.

## Filings

[`filings_service.get_filing_links`](../backend/services/dd_coach/filings_service.py) returns SEC EDGAR landing-page URLs for 10-K, 10-Q, 8-K, DEF14A, and Form 4. We deliberately link the landing page (not a specific filing URL) so the link survives new filings.

CIKs come from `services/fundamentals_service._load_cik_map()` which is already cached on disk. Unknown ticker → `DDEntryNotFound` (router maps to 404).

## Cuts from V1 (deferred or rejected)

| Idea | Status | Reason |
|---|---|---|
| Growth Killers checkbox row | Cut | Hard Rails cover the same ground without a separate UI surface. |
| Advanced sliders visible by default | Cut | V1 keeps Screen 5 to 6 fields max. Power users can reach them in V2. |
| Reverse-DCF check | Deferred | Adds a vocabulary the target user doesn't have. Revisit in V2. |
| Peer-median live multiple | Deferred | Requires peer-set curation per sector; expensive for low value-add. |
| Revisit-date scheduler | Deferred | Out of scope for the wizard; belongs to a separate Portfolio app. |
| Outcome marker (win/loss after N months) | Deferred | Same as above. |
| PEG ratio | Rejected | Misleading for both mature and growth profiles; we don't use it anywhere. |
| Stock-based-comp line item | Deferred | Covered indirectly via dilution % in the Growth Lens. |
| Size-override slider | Deferred | Screen 8 keeps three buttons (pass / small / standard) for V1. |
| Print / PDF export | Deferred | Cosmos doc already serializes the thesis; export is a frontend-only V2 task. |
| "Finance Mode" toggle | Rejected | Mixed vocabularies confuse the user; pick one and stay. |
| 5 of 8 sectors in defaults table | Deferred | V1 ships with the 3 sectors the screener already supports. |

## Lockstep checklist (for PR authors)

- [ ] Changed a hard-rail trigger or message? Update both `_compute_hard_rails` **and** the Hard Rails table above.
- [ ] Changed an auto-selector threshold? Update `select_method` **and** the Auto-Selector table.
- [ ] Changed a discount rate / years / dilution default? Update both `valuation_service` constants **and** the Method Math section.
- [ ] Added a sector? Update `SECTOR_MULTIPLES_*` **and** the two sector tables above.
- [ ] Changed an endpoint shape? Update both the router and the Scope table.
- [ ] Touched V2 completion validation (`assert_completable`) thresholds? Update both `models.BEAR_CASE_MIN_CHARS` / `BAIL_OUT_TRIGGER_MIN_CHARS` **and** the V2 Additions section below.
- [ ] Touched the peer-multiple band? Update `peer_multiples.py` **and** the Path-to-Target peer-band table.

---

# V2 Additions

## V2.1 Screen 1 — Forced reaction to red flags

If `DataCard.flags.reasons` is non-empty when the wizard loads, the user
cannot complete the entry without filling `answers.q1_flag_response`:

- `acknowledgment`: one of `accounted` / `changes_view` / `explained`.
- `note` (optional): one-sentence explanation.

This is the only conditional V2 completion gate — entries with a clean
data card don’t require this field. Enforced in
[`DDEntryDoc.assert_completable`](../backend/services/dd_coach/models.py).

## V2.2 Screen 5 — Leadership mini-screen

`answers.q5_leadership` captures four fields; the first two are required
for completion:

| Field | Required | Notes |
|---|---|---|
| `who` | yes | Free text — CEO name, tenure, founder vs hired |
| `insider_activity` | yes | enum: `heavy_buy` / `light_buy` / `quiet` / `light_sell` / `heavy_sell` / `unknown` |
| `comp_structure` | no | enum: `revenue` / `profit` / `stock` / `salary` / `unknown` |
| `concerns` | no | Free text |

The screen links to the existing Proxy (DEF14A) and Form-4 endpoints via
`FilingsBar` so the user can cross-reference without leaving the wizard.

## V2.3 Screen 6 — Path to Target (replaces “The Numbers”)

The V1 valuation auto-selector asked non-finance users to fill multi-input
forms they didn’t understand. V2 inverts the question: **the user picks a
target price, and we tell them what would have to be true for it to happen.**

[`path_to_target_service.get_path_to_target`](../backend/services/dd_coach/path_to_target_service.py) is a pure function over yfinance. Let:

- $S$ = spot price
- $T$ = user target price
- $R = T/S - 1$ — required total return
- $C$ = per-share cash basis (see basis selection below)
- $M = S/C$ — current multiple
- $P_\text{low}, P_\text{high}$ — peer-multiple band (sector-keyed)

We surface three paths:

| Path | Required growth (cash) | Required multiple |
|---|---|---|
| **A — Growth only** (“lemonade-stand grows”) | $R$ | unchanged at $M$ |
| **B — Multiple only** (“neighborhood gets trendy”) | $0$ | $T/C$ |
| **C — A bit of both** | $R/2$ | $M \cdot (1 + R/2)$ |

**Cash basis selection** (in `_pick_cash_basis`):

1. Use earnings (`trailingEps`) if positive **and** $\text{eps}/\text{revenue\_per\_share} \ge 2\%$.
2. Else use trailing FCF per share if positive.
3. Else: `applicable=False` on Paths A and C; only Path B with `applicable=False` too.

**Realism bands.**

Growth realism compares the required cash-growth rate to the 3-year revenue
CAGR (or to a 15% absolute baseline when no history is available):

| Bucket | Rule |
|---|---|
| `easy` | required ≤ baseline |
| `plausible` | required ≤ 1.5 × baseline |
| `stretch` | required ≤ 3 × baseline |
| `unrealistic` | required > 3 × baseline |

Multiple realism compares the required multiple to the sector peer high
$P_\text{high}$:

| Bucket | Rule |
|---|---|
| `easy` | required ≤ current $M$ |
| `plausible` | required ≤ $P_\text{high}$ |
| `stretch` | required ≤ 1.5 × $P_\text{high}$ |
| `unrealistic` | required > 1.5 × $P_\text{high}$ |

Path C’s realism is the *worse* (higher-rank) of its two component realisms.

**Peer-multiple bands.** Hardcoded in [`peer_multiples.py`](../backend/services/dd_coach/peer_multiples.py) and keyed by yfinance `info["sector"]`. Unknown sectors fall back to a broad-market band of 15–22×.

## V2.4 Screen 9 — Bear Case

`answers.q9_bear_case` is required for completion with a minimum of
`BEAR_CASE_MIN_CHARS = 30` characters. The wizard prompts the user to
“steelman” a 50%-loss scenario.

## V2.5 Screen 10 — Plan-Pre-Commit

`sizing` extends with five fields:

| Field | Required | Notes |
|---|---|---|
| `portfolio_pct_estimate` | no (advisory) | Frontend warns when > 5% |
| `sell_target` | yes (> 0) | Take-profit price |
| `add_more_price` | no | Buy-the-dip price |
| `bail_out_trigger` | yes (≥ `BAIL_OUT_TRIGGER_MIN_CHARS = 20` chars) | Specific bad-news condition |
| `commitment_acknowledged` | yes (must be `True`) | Explicit checkbox |

Front-end suggests a `valuation.user_call` from Path C’s realism (easy→cheap,
plausible→fair, stretch→expensive_worth_it, unrealistic→cannot_value); the
user can override.

## V2.6 Cuts table updates

| Idea | Previous status | New status | Reason |
|---|---|---|---|
| Peer-median live multiple | Deferred | **Shipped (V2)** via sector peer bands in `peer_multiples.py` (hardcoded, audit-friendly). |
| Reverse-DCF check | Deferred | **Shipped (V2)** as Path to Target — same idea, no jargon. |
| Print / PDF export | Deferred | Still deferred. |
| Outcome marker | Deferred | Still deferred; revisit-date scheduler too. |
