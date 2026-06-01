# DD Coach — Methodology (V1)

> Plain-English due-diligence wizard for non-finance retail investors.
> This doc and the code in `backend/services/dd_coach/` change together.
> If the math here disagrees with `valuation_service.py`, the code wins
> and this doc needs a PR.

## Mission

Walk a non-finance retail investor through 8 short screens that produce a
written investment thesis with a fair-value range — without ever showing
finance vocabulary or asking them to pick a model.

## Scope (V1, locked)

8 sequential screens, in this order:

1. **The Business** — Data Card + Hard Rails (auto). User confirms in their own words.
2. **What They Sell** — short text on revenue concentration.
3. **The Market** — TAM / share / growth.
4. **The Moat** — checkbox + one-liner (network / brand / switching / cost / IP).
5. **The Numbers** — auto-selected valuation method + bear/base/bull inputs.
6. **The Risks** — top 3 from 10-K Risk Factors, in plain English.
7. **Why Now** — catalyst within 12 months.
8. **Decision** — pass / size / wait; saved to Cosmos as a `DDEntryDoc`.

The frontend wizard renders these. The backend exposes:

| Endpoint | Purpose |
|---|---|
| `GET /api/dd_coach/data_card/{ticker}` | Screen 1 facts |
| `GET /api/dd_coach/filings/{ticker}` | SEC EDGAR landing-page links |
| `POST /api/dd_coach/valuation` | Screen 5 compute |
| `POST/GET/PATCH /api/dd_coach/entries` | CRUD on the saved thesis (Phase 0) |

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
