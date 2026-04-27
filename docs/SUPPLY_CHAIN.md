# Supply Chain — Methodology & Architecture

> How the Supply Chain tab turns a single ticker into a sourced, segment-aware DAG of suppliers, customers, and competitors.

---

## 1. End-to-end flow

```
User ticker (e.g. AAPL)
        │
        ▼
[Frontend] SupplyChainView.tsx → useSupplyChain hook
        │   GET /api/supply-chain?ticker=AAPL&enrich=filing+industry
        ▼
[Backend] routers/supply_chain.py  (rate-limited 3/min)
        │
        ▼
[services/supply_chain_service.get_supply_chain]
   1. SEC: ticker → CIK → latest 10-K + recent 8-Ks
   2. Extract Item 1 / 1A / 7 text (cap 600 KB) + 8-K text (cap 30 KB each)
   3. LLM Pass 1 — filing extraction (gpt-4.1, T=0.1)
   4. LLM Pass 2 — industry augmentation (T=0.2, opt-in)
   5. LLM Pass 3 — verifier audit (T=0.0)
   6. Merge + dedupe by name OR ticker (case-insensitive)
        │
        ▼
SupplyChainGraph → JSON
        │
        ▼
[Frontend] makeNodes(data) → ReactFlow DAG
   • focal node (center)
   • suppliers (left lane)   • customers (right lane)
   • competitors (bottom row)
   • multi-segment companies → vertical lanes per reportable segment
```

---

## 2. Data sourcing

### SEC pipeline
- **Ticker → CIK** via `https://www.sec.gov/files/company_tickers.json` (cached in-memory).
- **Latest 10-K** via `https://data.sec.gov/submissions/CIK{cik}.json`.
- **Recent 8-Ks**: up to **8 filings** filed on/after the 10-K date.
- **User-Agent header required** by SEC (set per `SEC_USER_AGENT` env).

### Text extraction (`_extract_relevant_text`)
Targets only:
| Section | Why |
|---|---|
| Item 1 — Business | Names suppliers, customers, segments |
| Item 1A — Risk Factors | "If Foundry X reduces capacity…" → dependency signals |
| Item 7 — MD&A | Segment-level customer concentration commentary |

- BeautifulSoup strips scripts/styles.
- Regex locates section boundaries (`item\s*1\b[.\s]\s*business`, etc.).
- Whitespace collapsed; total cap **600 KB** (trims from start, since front matter is noise).
- 8-Ks capped at **30 KB each**, prefixed with `--- 8-K filed YYYY-MM-DD ---`.

---

## 3. Three-pass LLM pipeline

All passes use **Azure OpenAI `gpt-4.1`**, `response_format={"type": "json_object"}`.

| Pass | Purpose | Temp | Source tag |
|---|---|---|---|
| **1. Filing extraction** | Pull relationships **named in filings** | 0.1 | `10-K` or `8-K` |
| **2. Industry augmentation** | Add publicly-known relationships **not** in filings | 0.2 | `industry` |
| **3. Verifier audit** | Drop unsupported industry entries, recalibrate confidence | 0.0 | (filters Pass 2) |

### Pass 1 — schema (excerpt)
```json
{
  "segments": ["Intelligent Cloud", "Productivity & Business Processes"],
  "concentration_note": "No single customer accounted for >10% of net revenue.",
  "suppliers": [
    {
      "name": "Taiwan Semiconductor Manufacturing",
      "ticker": "TSM",
      "relationship": "Foundry / chip fab",
      "cost_pct": null,
      "segment": "Intelligent Cloud",
      "source": "10-K",
      "notes": "5nm capacity for AI accelerators"
    }
  ],
  "customers": [...],
  "competitors": [...],
  "summary": "..."
}
```
Hard caps: **15 suppliers, 15 customers, 10 competitors**.

### Pass 2 — industry augmentation
Sees the focal company name, segments, and Pass-1 lists (compact JSON). Asked to ADD only relationships it can credibly cite:
- 0.9+ → textbook / officially announced multi-year
- 0.7–0.89 → widely reported, multiple sources
- 0.5–0.69 → sector-typical, not specifically confirmed
- < 0.5 → omit

Each addition must include `notes` citing a basis. Caps: 15 / 15 / **5** competitors.

### Pass 3 — verifier audit
Auditor persona, T=0. Reviews each Pass-2 candidate:
- DROP if no credible public basis. "When in doubt, DROP."
- DROP if final confidence < 0.6.
- ADJUST confidence downward if overstated.
- IMPROVE `notes` citation; never invent a citation.
- Cannot ADD entries or change `name`/`ticker`/`relationship`.

Returns the surviving subset plus a one-line `audit_summary`.

### Merge & dedupe
```python
seen = {(name.lower(), ticker.lower()) for n in filing_nodes}
for cand in verified_industry:
    key = (cand.name.lower(), cand.ticker.lower())
    if key not in seen:
        out.append(cand); seen.add(key)
```
Filing source always wins over industry on collision.

---

## 4. Data shapes

### Backend (`supply_chain_service.py`)
```python
SourceTag = Literal["10-K", "8-K", "industry"]

@dataclass
class CompanyNode:
    name: str
    ticker: Optional[str]
    relationship: str
    revenue_pct: Optional[float]   # customers only
    cost_pct: Optional[float]      # suppliers only
    notes: str
    source: SourceTag
    segment: Optional[str]
    confidence: Optional[float]    # populated only when source == "industry"

@dataclass
class SupplyChainGraph:
    ticker: str
    company_name: str
    filing_date: str               # 10-K date
    accession: str                 # SEC accession (audit trail)
    suppliers: list[CompanyNode]
    customers: list[CompanyNode]
    competitors: list[CompanyNode]
    summary: str
    eight_k_count: int
    eight_k_dates: list[str]
    segments: list[str]
    concentration_note: str        # verbatim from 10-K
    enrichment_used: list[str]     # ["filing"] or ["filing","industry","verified"]
    cached: bool
```

### Frontend ([frontend/src/types/supplyChain.ts](frontend/src/types/supplyChain.ts))
TypeScript mirror of the dataclass — exact same field names so JSON deserializes 1:1.

---

## 5. Graph topology

```
       SUPPLIERS               FOCAL                 CUSTOMERS
       (left lanes)            (center)              (right lanes)
                                  │
   ┌───────────┐                  │                ┌───────────┐
   │ TSM (10-K)│ ───────────────► │ ──────────────►│ AAPL (10-K)│
   └───────────┘                  │                └───────────┘
                              ┌───┴────┐
   ┌───────────┐              │ MSFT   │           ┌───────────┐
   │ NVDA (8-K)│ ───solid───► │        │ ──────►   │ AMZN (IND)│
   └───────────┘              └───┬────┘           └───────────┘
                                  │
                                  │ (dashed = inferred)
                                  ▼
                            COMPETITORS
                          ┌────┐ ┌────┐ ┌────┐
                          │AMD │ │INTC│ │GOOG│
                          └────┘ └────┘ └────┘
```

### Edge / node styling by source
| Source | Border | Edge | Badge |
|---|---|---|---|
| `10-K` | solid gray (`#94a3b8`) | solid gray | `10-K` |
| `8-K` | solid blue (`#60a5fa`) | solid blue | `8-K` |
| `industry` | **dashed** amber (`#fbbf24`) | dashed amber | `INF` |

### Multi-segment layout
When `segments.length >= 2`, suppliers and customers are bucketed into **vertical lanes per reportable segment**, plus a `Cross-segment` lane for nodes with no explicit segment attribution. Layout constants (in [frontend/src/components/SupplyChainView.tsx](frontend/src/components/SupplyChainView.tsx)):

```ts
FOCAL_X = 600        SUPPLIER_X = 100      CUSTOMER_X = 1100
NODE_VSPACE = 78     LANE_GAP = 60         LANE_HEADER_H = 28
COMP_HSPACE = 180    // competitors row at bottom
```

ReactFlow handles pan / zoom / fit-to-view (`fitView`, `minZoom=0.2`, `maxZoom=2`).

---

## 6. Per-node signals

| Field | Suppliers | Customers | Competitors |
|---|:-:|:-:|:-:|
| `name`, `ticker`, `relationship`, `notes`, `source` | ✓ | ✓ | ✓ |
| `segment` (reportable) | ✓ | ✓ | ✓ |
| `cost_pct` (% of focal COGS) | ✓ | — | — |
| `revenue_pct` (% of focal revenue) | — | ✓ | — |
| `confidence` (0..1) | only if `source=industry` | only if `source=industry` | only if `source=industry` |

`cost_pct` / `revenue_pct` are extracted only when the filing explicitly discloses them (e.g. "represents 22% of net sales"). Most modern 10-Ks now redact specific customer percentages, so these are sparse.

---

## 7. API contract

`GET /api/supply-chain`

| Param | Type | Default | Notes |
|---|---|---|---|
| `ticker` | string | required | regex `^[A-Za-z\.\-]+$`, max 10 chars |
| `refresh` | bool | `false` | bypass cache (cache layer is currently a no-op) |
| `enrich` | string | `filing+industry` | `filing` only, or `filing+industry` to trigger Passes 2–3 |

Response: `SupplyChainResponse` (Pydantic model — flat serialization of `SupplyChainGraph`).

### HTTP error codes
| Code | Cause |
|---|---|
| 400 | Invalid ticker pattern |
| 404 | Ticker not in SEC ticker map; no 10-K filing found |
| 429 | Rate limit (3/min per IP) |
| 500 | LLM call / JSON parse failure |
| 503 | Azure OpenAI not configured |

### Rate limits
- Global default: 60/min, 600/hour per IP.
- This endpoint: **3/min** (each call is 20–30 s of LLM work).

---

## 8. Validation & fallbacks

1. **Required env**: `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, `SEC_USER_AGENT`.
2. **Ticker resolution** — fail-fast with 404 if not in SEC map.
3. **Filing length** — warn if Item 1 slice < 5 KB (probably bad parse).
4. **JSON enforcement** — Azure `json_object` mode + `json.loads` raises on malformed output.
5. **Graceful degradation** — if Pass 2 fails, skip industry enrichment and return filing-only graph (`enrichment_used = ["filing"]`). If Pass 3 fails, fall back to unverified Pass-2 output and log a warning.
6. **Hard caps** applied after merge (`suppliers[:15]`, etc.) to bound payload size.

---

## 9. Frontend UX

- **Refresh button** → re-fetch with `refresh=true`.
- **Industry knowledge toggle** → switches between `enrich=filing` and `enrich=filing+industry`.
- **Legend overlay** (top-right): explains source color/dash conventions.
- **Detail panel** (click a node):
  - Ticker, name, source badge, segment, confidence (if inferred)
  - Relationship description
  - `cost_pct` or `revenue_pct` (if disclosed)
  - Full `notes` text
- **Metadata bar**: filing date, accession, 8-K count + dates, concentration note, segment list, `enrichment_used` provenance chips.

---

## 10. Files

| File | Lines (approx.) | Purpose |
|---|---|---|
| [backend/services/supply_chain_service.py](../backend/services/supply_chain_service.py) | ~630 | Dataclasses, SEC fetch, text extraction, 3-pass LLM, merge |
| [backend/routers/supply_chain.py](../backend/routers/supply_chain.py) | ~40 | FastAPI route + response model + rate limit |
| [frontend/src/types/supplyChain.ts](../frontend/src/types/supplyChain.ts) | ~30 | TypeScript contract |
| [frontend/src/hooks/useSupplyChain.ts](../frontend/src/hooks/useSupplyChain.ts) | ~33 | Fetch hook with error / loading state |
| [frontend/src/components/SupplyChainView.tsx](../frontend/src/components/SupplyChainView.tsx) | ~620 | ReactFlow render, layout, detail panel |

---

## 11. Known limitations / future work

1. **No persistent cache** — `cached` is always `false`; every request re-runs the full pipeline. Add Redis or file-based cache keyed on ticker + 10-K accession (invalidate when a newer 10-K appears).
2. **Modern 10-Ks rarely disclose customer %** — `revenue_pct` / `cost_pct` are mostly null. Could enrich from Bloomberg-style supplier-relationship datasets if licensed.
3. **Industry pass is recall-bounded by the model's training cutoff.** Recent partnerships (post training) won't appear unless they showed up in 8-Ks.
4. **Verifier is itself an LLM** — drops false positives well but cannot truly *verify*. A future pass could ground each industry candidate against a web-search snippet.
5. **No tier-2 expansion** — graph is one hop deep (focal → direct supplier). Multi-tier (focal → TSMC → ASML) would require recursive expansion and is bounded by token cost.
6. **Edges carry no flow magnitude** — width / opacity could encode `cost_pct` or `revenue_pct` when present.
