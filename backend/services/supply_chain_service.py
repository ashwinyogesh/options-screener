"""
Supply Chain extraction service.

Pipeline:
1. Resolve ticker -> SEC CIK
2. Fetch latest 10-K filing index
3. Download the primary document, extract text
4. Send relevant sections to Azure OpenAI (gpt-4.1) for structured extraction
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

import httpx
from bs4 import BeautifulSoup
from openai import AzureOpenAI

SourceTag = Literal["10-K", "8-K", "industry"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- Config -----
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Options Screener app@example.com")
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


# ---------------------------------------------------------------- Models -----
@dataclass
class CompanyNode:
    name: str
    ticker: Optional[str] = None
    relationship: str = ""        # e.g. "Foundry / chip fab", "Cloud customer"
    revenue_pct: Optional[float] = None  # % of focal company's revenue (if disclosed)
    cost_pct: Optional[float] = None     # % of focal company's COGS (if disclosed)
    notes: str = ""
    source: SourceTag = "10-K"           # provenance of this relationship
    segment: Optional[str] = None        # business segment, if known
    confidence: Optional[float] = None   # 0–1, only for inferred sources


@dataclass
class SupplyChainGraph:
    ticker: str
    company_name: str
    filing_date: str
    accession: str
    suppliers: list[CompanyNode] = field(default_factory=list)
    customers: list[CompanyNode] = field(default_factory=list)
    competitors: list[CompanyNode] = field(default_factory=list)
    summary: str = ""
    cached: bool = False
    eight_k_count: int = 0
    eight_k_dates: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    concentration_note: str = ""
    enrichment_used: list[str] = field(default_factory=list)


# ------------------------------------------------------------ Ticker -> CIK ----
_TICKER_CACHE: dict[str, str] | None = None


def _load_ticker_map() -> dict[str, str]:
    """Load ticker -> CIK map from SEC (cached in-memory)."""
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE
    url = "https://www.sec.gov/files/company_tickers.json"
    with httpx.Client(timeout=30, headers=SEC_HEADERS) as c:
        r = c.get(url)
        r.raise_for_status()
        data = r.json()
    mapping: dict[str, str] = {}
    for entry in data.values():
        t = entry["ticker"].upper()
        cik = str(entry["cik_str"]).zfill(10)
        mapping[t] = cik
    _TICKER_CACHE = mapping
    return mapping


def resolve_cik(ticker: str) -> Optional[str]:
    return _load_ticker_map().get(ticker.upper())


# --------------------------------------------------- SEC filings index -----
def _fetch_filings_index(cik: str) -> tuple[str, list[dict]]:
    """Return (company_name, list of recent filings with form/accession/date/primary_doc_url)."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    with httpx.Client(timeout=30, headers=SEC_HEADERS) as c:
        r = c.get(url)
        r.raise_for_status()
        data = r.json()
    company_name = data.get("name", "")
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    items = []
    for i, form in enumerate(forms):
        accession_clean = accessions[i].replace("-", "")
        items.append({
            "form": form,
            "accession": accessions[i],
            "filing_date": dates[i],
            "primary_doc_url": (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_clean}/{primary_docs[i]}"
            ),
        })
    return company_name, items


def _fetch_latest_10k(cik: str) -> Optional[dict]:
    """Return {accession, filing_date, primary_doc_url, company_name} for the latest 10-K."""
    company_name, items = _fetch_filings_index(cik)
    for item in items:
        if item["form"] == "10-K":
            return {**item, "company_name": company_name}
    return None


def _fetch_recent_8ks(cik: str, since_date: str, max_count: int = 8) -> list[dict]:
    """Return up to max_count 8-K filings filed on/after since_date (YYYY-MM-DD)."""
    _, items = _fetch_filings_index(cik)
    cutoff = datetime.strptime(since_date, "%Y-%m-%d").date()
    out: list[dict] = []
    for item in items:
        if item["form"] != "8-K":
            continue
        try:
            d = datetime.strptime(item["filing_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= cutoff:
            out.append(item)
        if len(out) >= max_count:
            break
    return out


# ---------------------------------------------------- Filing text extract ----
_SECTIONS_OF_INTEREST = re.compile(
    r"(?is)(item\s*1[a-c]?\.[\s\S]{1,80}?(business|risk\s+factors|customers|suppliers))"
)


def _extract_relevant_text(html: str, max_chars: int = 600_000) -> str:
    """
    Strip tags, keep the Business + Risk Factors sections (Item 1, 1A).
    These are where supplier/customer/competitor info lives.
    Falls back to the full text if section markers can't be located.
    """
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    text = soup.get_text(separator="\n")
    # Collapse whitespace
    text = re.sub(r"\n{2,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    # Heuristic: keep Item 1 (Business) + Item 1A (Risk Factors) + Item 7 (MD&A,
    # which contains segment commentary critical for diversified companies).
    # We slice [Item 1 Business -> Item 2 Properties] AND [Item 7 MD&A -> Item 8 Financials]
    # and concatenate. Many filings use non-breaking spaces or weird casing, so be lenient.
    m_biz_start = re.search(r"(?is)\bitem\s*1\b[.\s]\s*business\b", text)
    m_biz_end = re.search(r"(?is)\bitem\s*2\b[.\s]\s*properties\b", text)
    m_mda_start = re.search(
        r"(?is)\bitem\s*7\b[.\s]\s*management.s\s+discussion", text
    )
    m_mda_end = re.search(
        r"(?is)\bitem\s*8\b[.\s]\s*financial\s+statements", text
    )

    parts: list[str] = []
    if m_biz_start and m_biz_end and m_biz_end.start() > m_biz_start.start():
        biz = text[m_biz_start.start(): m_biz_end.start()]
        if len(biz) > 5000:
            parts.append(biz)
        else:
            logger.warning("Item 1 slice too short (%d chars)", len(biz))
    if m_mda_start and m_mda_end and m_mda_end.start() > m_mda_start.start():
        mda = text[m_mda_start.start(): m_mda_end.start()]
        if len(mda) > 3000:
            parts.append(mda)
        else:
            logger.warning("Item 7 slice too short (%d chars)", len(mda))

    if parts:
        text = "\n\n".join(parts)
    else:
        logger.warning("No section slices located - using full text")

    if len(text) > max_chars:
        # Trim from the start - early pages are usually TOC, cover, glossary
        text = text[len(text) - max_chars:]
    return text


def _fetch_filing_text(url: str) -> str:
    with httpx.Client(timeout=60, headers=SEC_HEADERS, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return _extract_relevant_text(r.text)


def _fetch_8k_text(url: str, max_chars: int = 30_000) -> str:
    """8-Ks are short; just strip tags and return whole document."""
    with httpx.Client(timeout=30, headers=SEC_HEADERS, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{2,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text[:max_chars]


# ----------------------------------------------- LLM structured extraction --
_SYSTEM_PROMPT = """You are a financial analyst extracting supply chain relationships from SEC filings.

You will receive the focal company's latest 10-K plus any recent 8-K filings (material event disclosures).
Merge information from BOTH sources into a single consolidated graph.
- Use 10-K as the foundation (suppliers, customers, competitors)
- Use 8-K filings to add NEW relationships announced since the 10-K (new contracts, customer wins, supplier changes)
- If 8-K info contradicts the 10-K, prefer the more recent 8-K
- Do NOT duplicate the same relationship across sources

Return a JSON object with this exact schema:
{
  "segments": ["<reportable business segment names from the filing, e.g. 'Intelligent Cloud', 'Productivity & Business Processes'>"],
  "concentration_note": "<verbatim or near-verbatim sentence describing customer/supplier concentration, e.g. 'No single customer accounted for more than 10% of net sales in fiscal 2024.' Empty string if not disclosed.>",
  "suppliers": [
    {
      "name": "<company name>",
      "ticker": "<stock ticker if publicly traded, else null>",
      "relationship": "<what they supply, e.g. 'Foundry/chip fab', 'Memory chips'>",
      "cost_pct": <% of focal company COGS/spending if disclosed, else null>,
      "segment": "<which segment this supplier serves, or null>",
      "source": "<'10-K' or '8-K'>",
      "notes": "<contract terms / 8-K filing date if applicable / qualitative info>"
    }
  ],
  "customers": [
    {
      "name": "...",
      "ticker": "...",
      "relationship": "<what they buy>",
      "revenue_pct": <% of focal company revenue if disclosed, else null>,
      "segment": "<which segment this customer buys from, or null>",
      "source": "<'10-K' or '8-K'>",
      "notes": "..."
    }
  ],
  "competitors": [
    {
      "name": "...",
      "ticker": "...",
      "relationship": "<segment/market they compete in>",
      "segment": "<which segment of focal company they compete with, or null>",
      "source": "<'10-K' or '8-K'>",
      "notes": "..."
    }
  ],
  "summary": "<2-3 sentences on the focal company's supply chain, mentioning notable shifts from recent 8-Ks>"
}

Rules:
- Only include companies clearly named in the filings
- Use the company's common name (e.g. "Taiwan Semiconductor Manufacturing" not "TSMC Holdings")
- If a ticker isn't standard (e.g. foreign-listed), still include it (e.g. "TSM", "005930.KS")
- Prefer publicly traded companies but include major private suppliers (e.g. "Foxconn")
- If a percentage is mentioned, extract it as a number (e.g. "represents 22% of revenue" -> 22.0)
- For `segment`: only fill if the filing explicitly attributes the relationship to a reportable segment; otherwise null
- For `source`: "10-K" unless the relationship is announced/disclosed only in an 8-K (then "8-K" with the date in `notes`)
- For `concentration_note`: capture customer-concentration disclosures (e.g. "top 5 customers = 41% of net sales", "no customer >10%"). This explains gaps in the customer list.
- Limit to top 15 suppliers, 15 customers, 10 competitors
- Return ONLY valid JSON, no markdown fences"""


_INDUSTRY_SYSTEM_PROMPT = """You are a financial analyst augmenting a supply-chain graph for a public company.

You will receive:
- The focal company name + ticker
- The reportable business segments
- The list of suppliers/customers/competitors already extracted from the company's SEC filings

Your task: ADD additional supplier/customer/competitor relationships that are PUBLICLY KNOWN but NOT mentioned in the filing-derived list. Use only widely reported, credible relationships from your training knowledge:
- Major announced partnerships, multi-year contracts covered in trade press
- Well-known customer relationships discussed in earnings calls or industry analysis
- Standard sector-typical suppliers (e.g. for a hyperscaler: NVIDIA for GPUs, Cisco/Arista for networking, Vertiv for power)
- Established competitors widely recognized in the industry

Return a JSON object with this exact schema:
{
  "suppliers": [{"name": "...", "ticker": "...", "relationship": "...", "cost_pct": null, "segment": "<segment served, or null>", "confidence": <0.0-1.0>, "notes": "<basis: e.g. 'Widely reported partnership announced 2023', 'Standard hyperscaler GPU supplier'>"}],
  "customers": [{"name": "...", "ticker": "...", "relationship": "...", "revenue_pct": null, "segment": "...", "confidence": <0.0-1.0>, "notes": "..."}],
  "competitors": [{"name": "...", "ticker": "...", "relationship": "...", "segment": "...", "confidence": <0.0-1.0>, "notes": "..."}]
}

CRITICAL rules:
- DO NOT duplicate any relationship that is already in the filing-derived list (match on name OR ticker, case-insensitive)
- DO NOT fabricate. If you are unsure or cannot recall a credible basis, OMIT the entry. Empty arrays are fine.
- `confidence`: 0.9+ for textbook/uncontested relationships (e.g. TSMC supplies NVIDIA), 0.7-0.9 for widely reported, 0.5-0.7 for likely but not certain. Below 0.5 = omit.
- Hard caps: at most 15 suppliers, 15 customers, 5 competitors
- For diversified companies, distribute additions across segments
- `notes` MUST cite the basis (e.g. 'Reported partnership 2023', 'Standard sector supplier', 'Discussed in Q3 2024 earnings call')
- Return ONLY valid JSON, no markdown fences"""


def _call_llm(filing_text: str, ticker: str, company_name: str, recent_8k_text: str = "") -> dict:
    if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT in backend/.env"
        )
    client = AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    user_parts = [
        f"Focal company: {company_name} ({ticker})",
        "",
        "=== 10-K excerpt (Item 1 Business + Risk Factors) ===",
        filing_text,
    ]
    if recent_8k_text:
        user_parts += [
            "",
            "=== Recent 8-K filings (material events since 10-K) ===",
            recent_8k_text,
        ]
    user_msg = "\n".join(user_parts)
    resp = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


def _call_industry_llm(
    ticker: str,
    company_name: str,
    segments: list[str],
    existing: dict,
) -> dict:
    """Second-pass call: ask the LLM to add publicly-known relationships not in the filing."""
    if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT in backend/.env"
        )
    client = AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    # Compact summary of existing list so the model can de-dupe
    def _compact(items: list[dict]) -> list[dict]:
        return [
            {"name": x.get("name"), "ticker": x.get("ticker")}
            for x in items
        ]
    payload = {
        "focal_company": company_name,
        "focal_ticker": ticker,
        "segments": segments,
        "existing_suppliers": _compact(existing.get("suppliers", [])),
        "existing_customers": _compact(existing.get("customers", [])),
        "existing_competitors": _compact(existing.get("competitors", [])),
    }
    user_msg = json.dumps(payload, indent=2)
    resp = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _INDUSTRY_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


_VERIFIER_SYSTEM_PROMPT = """You are an audit reviewer for a supply-chain analyst.

You will receive a list of CANDIDATE supplier/customer/competitor relationships that another analyst proposed for a focal public company, based on industry knowledge (NOT from the company's SEC filings). Your job is to AUDIT this list and return a filtered, calibrated version.

For EACH candidate, evaluate:
1. Is the relationship publicly known and credibly reported (press releases, earnings calls, mainstream tech/business press, partnership announcements)?
2. Is the proposed `confidence` score appropriate? Apply this calibration:
   - 0.9+ : Textbook / uncontested / officially announced multi-year relationship
   - 0.7-0.89 : Widely reported, multiple credible sources
   - 0.5-0.69 : Likely / sector-typical but not specifically confirmed
   - <0.5 : Unsupported / speculation
3. Is the basis citation in `notes` specific enough? (Vague notes like "industry standard" are weak; "Announced 2023 partnership" or "Disclosed in Q3 2024 earnings call" are strong.)

ACTIONS to take:
- DROP any candidate where you cannot recall a credible public basis. Be strict — when in doubt, DROP.
- DROP any candidate whose final confidence falls below 0.6.
- ADJUST `confidence` downward if the original was overstated.
- IMPROVE `notes` to cite a specific basis where possible (e.g., year of announcement, type of source). Never invent a citation; if you can only say "widely reported", that's fine.
- DO NOT add new candidates. DO NOT change `name`, `ticker`, `relationship`, `revenue_pct`, `cost_pct`, or `segment`.

Return JSON in this exact shape (same as input minus dropped entries):
{
  "suppliers": [ ... ],
  "customers": [ ... ],
  "competitors": [ ... ],
  "audit_summary": "<1 sentence: how many dropped, common reason>"
}

Return ONLY valid JSON, no markdown fences."""


def _call_verifier_llm(
    ticker: str,
    company_name: str,
    candidates: dict,
) -> dict:
    """Audit the industry-pass output: drop unsupportable entries, calibrate confidence."""
    if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT in backend/.env"
        )
    # Skip API call if there are no candidates to audit
    total = (
        len(candidates.get("suppliers", []))
        + len(candidates.get("customers", []))
        + len(candidates.get("competitors", []))
    )
    if total == 0:
        return candidates

    client = AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    payload = {
        "focal_company": company_name,
        "focal_ticker": ticker,
        "candidates": {
            "suppliers": candidates.get("suppliers", []),
            "customers": candidates.get("customers", []),
            "competitors": candidates.get("competitors", []),
        },
    }
    resp = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _VERIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        temperature=0.0,  # audit step: deterministic
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


# ----------------------------------------------------- Merge / dedupe utils ---
def _node_key(name: str | None, ticker: str | None) -> str:
    if ticker:
        return f"T:{ticker.upper().strip()}"
    return f"N:{(name or '').lower().strip()}"


def _coerce_company_node(raw: dict, default_source: SourceTag) -> CompanyNode:
    """Build a CompanyNode from an LLM dict, accepting only known fields."""
    allowed = set(CompanyNode.__dataclass_fields__.keys())
    clean: dict = {k: v for k, v in raw.items() if k in allowed}
    clean.setdefault("source", default_source)
    return CompanyNode(**clean)


def _merge_industry(
    base: list[CompanyNode], additions: list[dict], cap: int
) -> list[CompanyNode]:
    """Append non-duplicate industry-pass entries to the filing-grounded list."""
    seen = {_node_key(n.name, n.ticker) for n in base}
    out = list(base)
    for raw in additions:
        key = _node_key(raw.get("name"), raw.get("ticker"))
        if key in seen:
            continue
        node = _coerce_company_node(raw, default_source="industry")
        out.append(node)
        seen.add(key)
        if len(out) >= cap + len(base):
            break
    return out


# --------------------------------------------------------------- Public ----
def get_supply_chain(
    ticker: str,
    force_refresh: bool = False,
    enrich_industry: bool = True,
) -> SupplyChainGraph:
    ticker = ticker.upper()
    cik = resolve_cik(ticker)
    if not cik:
        raise ValueError(f"Ticker {ticker} not found in SEC database")

    filing = _fetch_latest_10k(cik)
    if not filing:
        raise ValueError(f"No 10-K filing found for {ticker}")

    accession = filing["accession"]
    company_name = filing["company_name"]

    # Fetch + extract
    filing_text = _fetch_filing_text(filing["primary_doc_url"])
    logger.info("10-K text extracted: %d chars for %s", len(filing_text), ticker)

    # Fetch recent 8-Ks filed since the 10-K to capture material events
    eight_ks = _fetch_recent_8ks(cik, since_date=filing["filing_date"], max_count=8)
    eight_k_text_parts: list[str] = []
    for ek in eight_ks:
        try:
            t = _fetch_8k_text(ek["primary_doc_url"])
            eight_k_text_parts.append(f"--- 8-K filed {ek['filing_date']} ---\n{t}")
        except Exception as e:
            logger.warning("Failed to fetch 8-K %s for %s: %s", ek["accession"], ticker, e)
    eight_k_text = "\n\n".join(eight_k_text_parts)
    logger.info("Loaded %d 8-Ks (%d chars total) for %s", len(eight_ks), len(eight_k_text), ticker)

    extracted = _call_llm(filing_text, ticker, company_name, recent_8k_text=eight_k_text)

    suppliers = [
        _coerce_company_node(c, default_source=c.get("source") or "10-K")
        for c in extracted.get("suppliers", [])
    ]
    customers = [
        _coerce_company_node(c, default_source=c.get("source") or "10-K")
        for c in extracted.get("customers", [])
    ]
    competitors = [
        _coerce_company_node(c, default_source=c.get("source") or "10-K")
        for c in extracted.get("competitors", [])
    ]
    segments = [s for s in extracted.get("segments", []) if isinstance(s, str) and s.strip()]
    enrichment_used = ["filing"]

    # Phase 3: industry-knowledge enrichment pass
    if enrich_industry:
        try:
            industry = _call_industry_llm(
                ticker, company_name, segments, extracted
            )
            raw_counts = (
                len(industry.get("suppliers", [])),
                len(industry.get("customers", [])),
                len(industry.get("competitors", [])),
            )

            # Verifier pass: audit the industry-pass output, drop unsupportable
            # entries and calibrate confidence scores.
            try:
                verified = _call_verifier_llm(ticker, company_name, industry)
                ver_counts = (
                    len(verified.get("suppliers", [])),
                    len(verified.get("customers", [])),
                    len(verified.get("competitors", [])),
                )
                logger.info(
                    "Verifier pass for %s: suppliers %d->%d, customers %d->%d, competitors %d->%d. %s",
                    ticker,
                    raw_counts[0], ver_counts[0],
                    raw_counts[1], ver_counts[1],
                    raw_counts[2], ver_counts[2],
                    verified.get("audit_summary", ""),
                )
                industry = verified
                enrichment_used.append("verified")
            except Exception as e:
                logger.warning("Verifier pass failed for %s (using raw industry output): %s", ticker, e)

            suppliers = _merge_industry(suppliers, industry.get("suppliers", []), cap=15)
            customers = _merge_industry(customers, industry.get("customers", []), cap=15)
            competitors = _merge_industry(competitors, industry.get("competitors", []), cap=5)
            enrichment_used.append("industry")
            logger.info(
                "Industry pass added %d suppliers, %d customers, %d competitors for %s",
                len(industry.get("suppliers", [])),
                len(industry.get("customers", [])),
                len(industry.get("competitors", [])),
                ticker,
            )
        except Exception as e:
            logger.warning("Industry enrichment pass failed for %s: %s", ticker, e)

    graph = SupplyChainGraph(
        ticker=ticker,
        company_name=company_name,
        filing_date=filing["filing_date"],
        accession=accession,
        suppliers=suppliers,
        customers=customers,
        competitors=competitors,
        summary=extracted.get("summary", ""),
        cached=False,
        eight_k_count=len(eight_ks),
        eight_k_dates=[ek["filing_date"] for ek in eight_ks],
        segments=segments,
        concentration_note=extracted.get("concentration_note", "") or "",
        enrichment_used=enrichment_used,
    )

    return graph
