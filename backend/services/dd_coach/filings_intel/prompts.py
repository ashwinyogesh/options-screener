"""System prompts and JSON schemas for filings_intel LLM calls.

One prompt + one schema per insight type. Prompts are deliberately
opinionated: they assume the user is a retail investor and demand
plain-English output with explicit caveats when evidence is thin.
"""
from __future__ import annotations

from typing import Any

InsightType = str  # business_summary | risk_diff | mda_summary | leadership | bear_scaffold

VALID_INSIGHT_TYPES: tuple[str, ...] = (
    "business_summary",
    "risk_diff",
    "mda_summary",
    "leadership",
    "bear_scaffold",
)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_BASE_RULES = (
    "You are an investment-research analyst writing for a retail investor. "
    "Use plain English, no MBA jargon. Never invent numbers; if the source "
    "text doesn't contain a fact, omit it rather than guess. Quote short "
    "phrases from the filing when helpful (under 25 words each) but do not "
    "paste long verbatim sections. Be candid about uncertainty."
)


BUSINESS_SUMMARY_SYSTEM = (
    f"{_BASE_RULES}\n\n"
    "Task: summarise WHAT this company actually does, in language a "
    "non-expert can follow. Identify their main products/services, who "
    "actually pays them, and one plausible competitive moat hypothesis. "
    "If the business has multiple segments, name them. Return strictly "
    "the JSON shape requested."
)


RISK_DIFF_SYSTEM = (
    f"{_BASE_RULES}\n\n"
    "Task: review two consecutive Risk Factors sections (this year's vs "
    "last year's 10-K) and surface the risks a careful investor needs to "
    "understand before buying. There are THREE buckets:\n\n"
    "1) new_risks — risks genuinely NEW this year (not present last year "
    "in any form).\n"
    "2) expanded_risks — risks materially EXPANDED this year: more "
    "specific language, named threats, new dollar figures, named "
    "regulators, named customers, or new geographies. A two-sentence "
    "boilerplate becoming a five-paragraph discussion is material.\n"
    "3) ongoing_risks — the TOP risks present in BOTH years that remain "
    "materially relevant to the investment case. These are NOT changes; "
    "they're the durable, still-active risks an investor must accept to "
    "own the stock. Surface 3-5 of the most company-specific ones.\n\n"
    "What to EXCLUDE from ALL three buckets (be ruthless):\n"
    "  - Risks that could apply to ANY large public company (generic "
    "cybersecurity, generic macro, generic talent retention) UNLESS the "
    "filing names a specific company-level consequence.\n"
    "  - 'Catch-all' risks ('other factors may adversely affect us').\n"
    "  - For new_risks specifically: re-worded versions of last year's "
    "text with no substantive change — those belong in ongoing_risks if "
    "still material, or nowhere at all.\n\n"
    "Per risk in ANY bucket you must provide:\n"
    "  - title: 3-7 words, concrete. NOT 'AI integration risks' — prefer "
    "'New AI feature rollout could miss FY26 revenue targets'.\n"
    "  - summary (new_risks, ongoing_risks) OR what_changed (expanded_risks): "
    "1-2 sentences, plain English, naming the specific product / segment / "
    "geography / customer / regulator the filing mentions. Avoid the words "
    "'challenges', 'uncertainties', 'may impact'.\n"
    "  - quote: a SHORT verbatim phrase (max 25 words) from THIS year's "
    "filing that proves the risk is real and shows the company's framing.\n"
    "  - why_it_matters: one sentence telling a retail investor the "
    "concrete consequence — which line of the P&L, which growth thesis, "
    "which customer relationship is at stake.\n"
    "  - severity: low / medium / high. DISTRIBUTE meaningfully — if you "
    "surface 5 risks they should NOT all be medium. Reserve 'high' for "
    "risks that could plausibly cut earnings power by 10%+ or trigger a "
    "re-rating; reserve 'low' for contained items.\n"
    "  - severity_rationale: one sentence justifying the severity choice "
    "in concrete terms (revenue exposure, customer concentration, "
    "regulatory teeth).\n\n"
    "Caps: AT MOST 5 new_risks, AT MOST 5 expanded_risks, AT MOST 5 "
    "ongoing_risks. Empty arrays are fine — padding with weak items is "
    "worse than honesty. The overall_tone field describes the year-over-year "
    "shift in the section as a whole."
)


MDA_SUMMARY_SYSTEM = (
    f"{_BASE_RULES}\n\n"
    "Task: read management's discussion (MD&A) and explain in plain "
    "English: (a) why revenue grew or shrank (the revenue bridge), "
    "(b) what's driving margins up or down, (c) the liquidity picture "
    "(cash, debt, buybacks), and (d) the tone of forward-looking "
    "commentary. Be specific about the direction and approximate "
    "magnitude — 'gross margin up roughly 200 bps on lower input costs'."
)


LEADERSHIP_SYSTEM = (
    f"{_BASE_RULES}\n\n"
    "Task: assess the leadership team for a retail investor. Inputs are "
    "the DEF 14A proxy (named executives, comp structure, summary "
    "compensation table) and a metadata summary of recent Form 4 insider "
    "filings (counts + dates, no transaction detail). Output: the CEO's "
    "name and how long they've been CEO if stated; whether compensation "
    "is aligned with shareholders (revenue/profit/stock vs salary-heavy); "
    "what the Form 4 cadence suggests qualitatively (heavy/light "
    "activity); and 1-3 specific concerns to flag. Be conservative — "
    "Form 4 metadata alone cannot tell you buys vs sells."
)


BEAR_SCAFFOLD_SYSTEM = (
    f"{_BASE_RULES}\n\n"
    "Task: using the company's stated risk factors and the business "
    "summary as context, scaffold THREE distinct plausible scenarios in "
    "which the stock would fall roughly 50% over the next 1-3 years. "
    "These are not predictions — they are stress-tests for the investor's "
    "thesis. Each scenario should be specific to this company (not generic "
    "'recession hits'); name the metric an investor should monitor; give "
    "a rough subjective probability range (e.g. '5-15%')."
)


SYSTEM_PROMPTS: dict[str, str] = {
    "business_summary": BUSINESS_SUMMARY_SYSTEM,
    "risk_diff": RISK_DIFF_SYSTEM,
    "mda_summary": MDA_SUMMARY_SYSTEM,
    "leadership": LEADERSHIP_SYSTEM,
    "bear_scaffold": BEAR_SCAFFOLD_SYSTEM,
}


# ---------------------------------------------------------------------------
# JSON schemas (Azure OpenAI strict json_schema)
# ---------------------------------------------------------------------------

def _schema(name: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    }


BUSINESS_SUMMARY_SCHEMA = _schema(
    "business_summary",
    {
        "summary": {"type": "string", "description": "2-4 sentence plain-English description"},
        "primary_products": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Distinct product / service lines",
        },
        "main_customers": {
            "type": "string",
            "description": "Who actually pays the company (one paragraph)",
        },
        "moat_hypothesis": {
            "type": "string",
            "description": "One plausible competitive moat or 'unclear from filing'",
        },
        "segments": {"type": "array", "items": {"type": "string"}},
    },
    ["summary", "primary_products", "main_customers", "moat_hypothesis", "segments"],
)


RISK_DIFF_SCHEMA = _schema(
    "risk_diff",
    {
        "new_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "quote": {
                        "type": "string",
                        "description": "Short verbatim phrase from the filing (<= 25 words)",
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": "One-sentence concrete investor consequence",
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "severity_rationale": {
                        "type": "string",
                        "description": "One sentence justifying the severity",
                    },
                },
                "required": [
                    "title",
                    "summary",
                    "quote",
                    "why_it_matters",
                    "severity",
                    "severity_rationale",
                ],
            },
        },
        "expanded_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "what_changed": {"type": "string"},
                    "quote": {
                        "type": "string",
                        "description": "Short verbatim phrase from THIS year's filing (<= 25 words)",
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": "One-sentence concrete investor consequence",
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "severity_rationale": {
                        "type": "string",
                        "description": "One sentence justifying the severity",
                    },
                },
                "required": [
                    "title",
                    "what_changed",
                    "quote",
                    "why_it_matters",
                    "severity",
                    "severity_rationale",
                ],
            },
        },
        "overall_tone": {
            "type": "string",
            "enum": ["materially worse", "modestly worse", "unchanged", "modestly better"],
        },
        "ongoing_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "quote": {
                        "type": "string",
                        "description": "Short verbatim phrase from THIS year's filing (<= 25 words)",
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": "One-sentence concrete investor consequence",
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "severity_rationale": {
                        "type": "string",
                        "description": "One sentence justifying the severity",
                    },
                },
                "required": [
                    "title",
                    "summary",
                    "quote",
                    "why_it_matters",
                    "severity",
                    "severity_rationale",
                ],
            },
        },
    },
    ["new_risks", "expanded_risks", "overall_tone", "ongoing_risks"],
)


MDA_SUMMARY_SCHEMA = _schema(
    "mda_summary",
    {
        "revenue_bridge": {"type": "string"},
        "margin_drivers": {"type": "string"},
        "liquidity": {"type": "string"},
        "forward_tone": {
            "type": "string",
            "enum": ["optimistic", "cautious", "neutral", "guarded"],
        },
        "highlights": {"type": "array", "items": {"type": "string"}},
    },
    ["revenue_bridge", "margin_drivers", "liquidity", "forward_tone", "highlights"],
)


LEADERSHIP_SCHEMA = _schema(
    "leadership",
    {
        "ceo_name": {"type": "string"},
        "ceo_tenure_note": {"type": "string", "description": "Free-text e.g. 'CEO since 2014' or 'tenure not stated'"},
        "comp_alignment": {
            "type": "string",
            "enum": ["heavily stock-linked", "performance-linked", "mixed", "salary-heavy", "unclear"],
        },
        "comp_summary": {"type": "string"},
        "insider_activity_note": {"type": "string", "description": "What the Form 4 cadence suggests qualitatively"},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    [
        "ceo_name",
        "ceo_tenure_note",
        "comp_alignment",
        "comp_summary",
        "insider_activity_note",
        "concerns",
    ],
)


BEAR_SCAFFOLD_SCHEMA = _schema(
    "bear_scaffold",
    {
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "narrative": {"type": "string"},
                    "probability_range_pct": {"type": "string", "description": "e.g. '5-15%'"},
                    "metric_to_watch": {"type": "string"},
                },
                "required": ["title", "narrative", "probability_range_pct", "metric_to_watch"],
            },
        }
    },
    ["scenarios"],
)


SCHEMAS: dict[str, dict[str, Any]] = {
    "business_summary": BUSINESS_SUMMARY_SCHEMA,
    "risk_diff": RISK_DIFF_SCHEMA,
    "mda_summary": MDA_SUMMARY_SCHEMA,
    "leadership": LEADERSHIP_SCHEMA,
    "bear_scaffold": BEAR_SCAFFOLD_SCHEMA,
}
