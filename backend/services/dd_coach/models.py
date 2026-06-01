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


class ValuationResult(BaseModel):
    """Bear/base/bull per-share fair value (Maturity Discount & Multiple methods)."""

    model_config = ConfigDict(extra="ignore")

    bear: float | None = None
    base: float | None = None
    bull: float | None = None
    spot: float | None = None


class Valuation(BaseModel):
    """Q5 inputs + outcome. Empty on draft until user reaches Q5."""

    model_config = ConfigDict(extra="ignore")

    method: ValuationMethod | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: ValuationResult | None = None
    user_call: UserCall | None = None
    reasoning: str | None = None


class Sizing(BaseModel):
    """Position size + stomach test outcome."""

    model_config = ConfigDict(extra="ignore")

    planned_dollars: float | None = None
    stomach_answer: StomachAnswer | None = None
    final_dollars: float | None = None


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

        Required: Q1-Q4 non-empty, Q5 user_call set, sizing.final_dollars > 0,
        stomach_answer set.
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
        if missing:
            raise DDEntryInvalid(
                "Entry cannot be completed — missing required fields: "
                + ", ".join(missing),
            )


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
