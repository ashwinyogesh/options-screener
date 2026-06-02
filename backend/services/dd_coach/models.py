"""Pydantic models for DD Coach entries.

The Cosmos doc shape is documented in docs/DD_COACH_METHODOLOGY.md (TBD).
Models are lenient for drafts (most fields optional) — completion enforces
required content via `DDEntryDoc.assert_completable()`.

Pydantic v2 conventions:
  - `model_config = ConfigDict(extra="ignore")` to tolerate older docs
  - `model_dump(mode="json")` for Cosmos persistence (datetimes → ISO strings)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---- Enums ----------------------------------------------------------------


class EntryStatus(str, Enum):
    DRAFT = "draft"
    COMPLETED = "completed"


class ValuationMethod(str, Enum):
    MULTIPLE_BASED = "multiple_based"      # profitable, stable (e.g., MSFT)
    MATURITY_DISCOUNT = "maturity_discount"  # growing, unprofitable (e.g., NBIS)
    OPTIONALITY = "optionality"            # pre-commercial / binary (e.g., IONQ)


class UserCall(str, Enum):
    CHEAP = "cheap"
    FAIR = "fair"
    EXPENSIVE_WORTH_IT = "expensive_worth_it"
    CANNOT_VALUE = "cannot_value"


class StomachAnswer(str, Enum):
    YES = "yes"
    UNSURE = "unsure"
    NO = "no"


class FlagAcknowledgment(str, Enum):
    """How the user reacted to a data-card red flag on Screen 1."""
    ACCOUNTED = "accounted"          # "I've factored it in"
    CHANGES_VIEW = "changes_view"    # "I'll size smaller / wait"
    EXPLAINED = "explained"          # "I read the inline explainer"


class InsiderActivity(str, Enum):
    HEAVY_BUY = "heavy_buy"
    LIGHT_BUY = "light_buy"
    QUIET = "quiet"
    LIGHT_SELL = "light_sell"
    HEAVY_SELL = "heavy_sell"
    UNKNOWN = "unknown"


class CompStructure(str, Enum):
    REVENUE = "revenue"
    PROFIT = "profit"
    STOCK = "stock"
    SALARY = "salary"
    UNKNOWN = "unknown"


# ---- Validation thresholds (v2) -------------------------------------------
# Hardcoded here so completion validation is auditable and easy to tune.

BEAR_CASE_MIN_CHARS = 30
BAIL_OUT_TRIGGER_MIN_CHARS = 20


# ---- Nested doc sections --------------------------------------------------


class Answers(BaseModel):
    """Free-text wizard answers. All optional on draft; only the original
    Q1-Q4 are required for completion (see ``DDEntryDoc.assert_completable``).

    Q1/Q2/Q3/Q4 back the four mandatory sections of the thesis. The
    ``q3_market``/``q3_moat``/``q3_why_now`` triple is captured separately
    so the UI's 8-screen wizard can persist each screen verbatim while still
    composing the canonical ``q3_upside`` rollup for completion.
    """

    model_config = ConfigDict(extra="ignore")

    q1_business: str | None = None
    q2_revenue_model: str | None = None
    q3_upside: str | None = None
    q4_risks: str | None = None

    # V1 wizard granular screens (optional; UI composes q3_upside from these).
    q3_market: str | None = None
    q3_moat: str | None = None
    q3_why_now: str | None = None

    # V2 additions ---------------------------------------------------------
    # Screen 1 — forced reaction to a data-card red flag (only required
    # for completion if the snapshot had flags at draft time).
    q1_flag_response: "FlagResponse | None" = None

    # Screen 5 — leadership mini-screen. The two important fields
    # (`who` and `insider_activity`) are required for completion.
    q5_leadership: "LeadershipCheck | None" = None

    # Screen 9 — steel-manned bear case. Required, min length enforced.
    q9_bear_case: str | None = None


class FlagResponse(BaseModel):
    """Screen 1 — how the user reacted to a flagged red item."""

    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    acknowledgment: FlagAcknowledgment
    note: str | None = None


class LeadershipCheck(BaseModel):
    """Screen 5 — leadership / insider / comp / concerns."""

    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    who: str | None = None
    insider_activity: InsiderActivity | None = None
    comp_structure: CompStructure | None = None
    concerns: str | None = None


class ValuationResult(BaseModel):
    """Bear/base/bull per-share fair value (Maturity Discount & Multiple methods)."""

    model_config = ConfigDict(extra="ignore")

    bear: float | None = None
    base: float | None = None
    bull: float | None = None
    spot: float | None = None


class GuidedValuationSave(BaseModel):
    """Persisted Fair Price screen state — user inputs + computed fair values.

    Added in V3. Stored inside ``Valuation.guided`` so older Cosmos docs
    (which lack this field) continue to deserialise cleanly.
    """

    model_config = ConfigDict(extra="ignore")

    # User-owned inputs
    current_eps: float | None = None
    growth_bear: float | None = None   # decimal, e.g. 0.05 = 5 %/yr
    growth_base: float | None = None
    growth_bull: float | None = None
    years: int = 5
    pe_bear: float | None = None
    pe_base: float | None = None
    pe_bull: float | None = None
    required_return: float = 0.12
    required_mos: float | None = None   # user's minimum margin of safety

    # Computed at calculation time
    fair_bear: float | None = None
    fair_base: float | None = None
    fair_bull: float | None = None
    spot_at_time: float | None = None
    margin_of_safety: float | None = None    # (base − spot) / base
    buy_at_or_below: float | None = None     # base × (1 − required_mos)


class Valuation(BaseModel):
    """Q5 inputs + outcome. Empty on draft until user reaches Q5."""

    model_config = ConfigDict(extra="ignore")

    method: ValuationMethod | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: ValuationResult | None = None
    user_call: UserCall | None = None
    reasoning: str | None = None
    guided: GuidedValuationSave | None = None   # V3 Fair Price screen


class Sizing(BaseModel):
    """Position size + stomach test outcome + V2 pre-commit plan."""

    model_config = ConfigDict(extra="ignore")

    planned_dollars: float | None = None
    stomach_answer: StomachAnswer | None = None
    final_dollars: float | None = None

    # V2 plan-pre-commit. Sell target + bail-out + acknowledgment are
    # required for completion. Add-more and portfolio % are optional.
    portfolio_pct_estimate: float | None = None  # advisory; warn if > 5
    sell_target: float | None = None
    add_more_price: float | None = None
    bail_out_trigger: str | None = None
    commitment_acknowledged: bool = False


# ---- Top-level doc --------------------------------------------------------

DEFAULT_USER_ID = "default"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DDEntryDoc(BaseModel):
    """The persisted Cosmos document for a DD entry.

    `id` and `ticker` together identify the document (ticker is the partition
    key). The service layer is responsible for ensuring `ticker` is uppercased
    consistently before persistence.
    """

    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    ticker: str
    user_id: str = DEFAULT_USER_ID

    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    completed_at: str | None = None
    status: EntryStatus = EntryStatus.DRAFT

    # Snapshot of the data card shown to the user at decision time.
    # Free-form JSON to keep this layer decoupled from Phase 1's card schema.
    data_card_snapshot: dict[str, Any] = Field(default_factory=dict)

    answers: Answers = Field(default_factory=Answers)
    valuation: Valuation = Field(default_factory=Valuation)
    sizing: Sizing = Field(default_factory=Sizing)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, v: str) -> str:
        return v.strip().upper()

    # ----- domain rules -----

    def is_completed(self) -> bool:
        # use_enum_values=True means status is stored as a string after dump,
        # but during in-process use it can be either; coerce both ways.
        s = self.status.value if isinstance(self.status, EntryStatus) else self.status
        return s == EntryStatus.COMPLETED.value

    def assert_completable(self) -> None:
        """Raise DDEntryInvalid if any required-for-completion field is missing.

        V1 required: Q1-Q4 non-empty, Q5 user_call set, sizing.final_dollars > 0,
        stomach_answer set.

        V2 additions:
          - q1_flag_response is required when the data_card_snapshot at draft
            time had any flagged reasons (``flags.balance_sheet_red`` etc.).
          - q5_leadership.who and q5_leadership.insider_activity required.
          - q9_bear_case >= 30 chars.
          - sizing.sell_target > 0.
          - sizing.bail_out_trigger >= 20 chars.
          - sizing.commitment_acknowledged is True.
        """
        from services.dd_coach.errors import DDEntryInvalid

        missing: list[str] = []
        a = self.answers
        if not (a.q1_business or "").strip():
            missing.append("answers.q1_business")
        if not (a.q2_revenue_model or "").strip():
            missing.append("answers.q2_revenue_model")
        if not (a.q3_upside or "").strip():
            missing.append("answers.q3_upside")
        if not (a.q4_risks or "").strip():
            missing.append("answers.q4_risks")
        if self.valuation.user_call is None:
            missing.append("valuation.user_call")
        if self.sizing.stomach_answer is None:
            missing.append("sizing.stomach_answer")
        if self.sizing.final_dollars is None or self.sizing.final_dollars <= 0:
            missing.append("sizing.final_dollars")

        # ---- V2 required fields ----
        if self._snapshot_had_flags() and a.q1_flag_response is None:
            missing.append("answers.q1_flag_response")
        lead = a.q5_leadership
        if lead is None or not (lead.who or "").strip():
            missing.append("answers.q5_leadership.who")
        if lead is None or lead.insider_activity is None:
            missing.append("answers.q5_leadership.insider_activity")
        bear = (a.q9_bear_case or "").strip()
        if len(bear) < BEAR_CASE_MIN_CHARS:
            missing.append(
                f"answers.q9_bear_case (min {BEAR_CASE_MIN_CHARS} chars)",
            )
        s = self.sizing
        if s.sell_target is None or s.sell_target <= 0:
            missing.append("sizing.sell_target")
        bail = (s.bail_out_trigger or "").strip()
        if len(bail) < BAIL_OUT_TRIGGER_MIN_CHARS:
            missing.append(
                f"sizing.bail_out_trigger (min {BAIL_OUT_TRIGGER_MIN_CHARS} chars)",
            )
        if not s.commitment_acknowledged:
            missing.append("sizing.commitment_acknowledged")

        if missing:
            raise DDEntryInvalid(
                "Entry cannot be completed — missing required fields: "
                + ", ".join(missing),
            )

    def _snapshot_had_flags(self) -> bool:
        """True if the data card snapshot captured at draft time had any
        non-empty flag reasons. Mirrors ``DataCard.flags.reasons`` shape
        from the data_card_service.
        """
        flags = self.data_card_snapshot.get("flags") if isinstance(
            self.data_card_snapshot, dict,
        ) else None
        if not isinstance(flags, dict):
            return False
        reasons = flags.get("reasons")
        return isinstance(reasons, list) and len(reasons) > 0


# ---- Input shapes for the router ------------------------------------------


class CreateEntryInput(BaseModel):
    """Payload to POST /api/dd_coach/entries."""

    model_config = ConfigDict(extra="ignore")

    ticker: str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Za-z\.\-]+$")


class PatchEntryInput(BaseModel):
    """Payload to PATCH /api/dd_coach/entries/{id}.

    All fields optional — the frontend autosaves whichever screen advanced.
    Service layer applies these as a partial merge over the existing doc.
    """

    model_config = ConfigDict(extra="ignore")

    data_card_snapshot: dict[str, Any] | None = None
    answers: Answers | None = None
    valuation: Valuation | None = None
    sizing: Sizing | None = None


# Resolve the forward references inside ``Answers`` now that the nested
# classes (FlagResponse, LeadershipCheck) are in scope.
Answers.model_rebuild()
