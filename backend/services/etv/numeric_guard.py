"""Closed-world numeric guard for ETV stage outputs.

Every numeric leaf in a stage's JSON output must be justifiable as one of:

* **grounded**   — within ±tolerance of a value present in ``EtvGrounding``,
                   optionally after scaling by {1, 1/100, 100, 1e6, 1e9} to
                   bridge fraction↔percent and unit conventions.
* **declared**   — equals (within tolerance) a value declared in
                   ``missing_inputs`` as ``ASSUMPTION:{name}={value}``.
* **derived**    — appears as the right-hand side of any ``derivation`` line
                   (``"foo = grounding.x * 1.1 = 220.5"``); the LLM has shown
                   its work and the critic stage will spot-check the algebra.
* **unjustified** — none of the above → reported for caller to action
                   (typically: route back to the offending stage with the
                   guard report appended to the prompt for one retry).

Whitelisted passthroughs (probabilities, current_price echoes, small
integers in 1-100 range used as counts/percentages/years) are exempt.

This module is *pure* and side-effect-free: callers decide what to do with
the report.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Iterator

# ----------------------------------------------------------- Constants ---

_TOLERANCE_FRAC = 0.005  # 0.5%
_TOLERANCE_ABS = 0.01    # for near-zero values

# JSON keys whose numeric values are *echoes* of inputs or schema-driven
# scalars — never treated as "model-generated numbers".
_PASSTHROUGH_KEYS: frozenset[str] = frozenset({
    "probability_pct",
    "current_price",
    "as_of",
    # Probability-weighted aggregates are validated by the deterministic
    # post-validator, not the numeric guard.
    "probability_weighted_etv",
    "weighted_decomposition_sum",
    "expected_return_pct",
    "central_estimate",
    "low_range",
    "high_range",
    # Asymmetry block is computed deterministically by validator.py.
    "upside_pct_weighted",
    "downside_pct_weighted",
    "ratio",
    # Cap & rubric scalars hard-coded in prompts.
    "confidence_pct",
    # Likelihood ratio is a subjective judgment scalar in [0.25, 4.0],
    # server-clamped and consumed by the IV-posterior step.  It is not a
    # valuation-model derived number and should not require grounding,
    # ASSUMPTION declaration, or derivation.
    "likelihood_ratio",
    "lr_rationale",
})

# Path-suffix patterns (substring match) to treat as passthrough — covers
# nested fields like `validation.warnings_count`.
_PASSTHROUGH_PATH_HINTS: tuple[str, ...] = (
    "validation.",
    "cache_age_sec",
)

# Leaf-name suffixes that imply a non-valuation scalar (count, year, months,
# percent-as-integer). When the leaf key ends with one of these, the value
# is exempt from the guard regardless of magnitude.
_PASSTHROUGH_KEY_SUFFIXES: tuple[str, ...] = (
    "_count",
    "_year",
    "_months",
    "_days",
    "_years",
    "_n",
)

# Scaling factors to try when matching against grounding values.
# Covers: identity, fraction↔percent, raw↔millions, raw↔billions.
_SCALE_FACTORS: tuple[float, ...] = (1.0, 100.0, 0.01, 1e6, 1e-6, 1e9, 1e-9)

# Regex for declared assumptions inside missing_inputs strings.
# Example: "wacc: ASSUMPTION used = 0.09 (sector median)"
#       or "rev_growth: ASSUMPTION=0.12 (consensus)"
_ASSUMPTION_RE = re.compile(
    r"ASSUMPTION[^=]*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
)

# Regex for derivation lines: capture the *final* number after the last `=`.
# Allows an optional trailing provenance tag (M1, v3-final), e.g.
#   "net_debt = 55000 [from grounding] - 75000 [from grounding] = -20000"
#   "net_debt = ... = -20000 [derived]"
_DERIVATION_FINAL_RE = re.compile(
    r"=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?:\[[^\]]*\])?\s*$",
)

# Regex for provenance tags inside a derivation line (M1, v3-final).
# Matches `[from grounding]`, `[ASSUMED]`, `[derived]` (case-insensitive).
_PROVENANCE_TAG_RE = re.compile(r"\[\s*([A-Za-z][A-Za-z _-]*)\s*\]")
_ASSUMED_TAG_RE = re.compile(r"\[\s*assumed\s*\]", re.IGNORECASE)

# Threshold above which a stage output is flagged ``assumption_heavy``
# (>3 ASSUMED-tagged numeric leaves in a single S2 recipe output, per the
# v3-final preamble RULE A).
_ASSUMPTION_HEAVY_THRESHOLD = 3


# ----------------------------------------------------------- Dataclasses ---

@dataclass(frozen=True)
class Unjustified:
    """A numeric leaf the guard could not classify."""
    path: str
    value: float
    nearest_grounded_field: str | None = None
    nearest_grounded_value: float | None = None
    nearest_distance_pct: float | None = None


@dataclass
class GuardReport:
    """Outcome of one guard pass over a stage's JSON output."""
    unjustified: list[Unjustified] = field(default_factory=list)
    grounded_count: int = 0
    declared_count: int = 0
    derived_count: int = 0
    passthrough_count: int = 0
    total_numbers: int = 0
    # Phase 2 additions ---------------------------------------------------
    # Count of `[ASSUMED]` provenance tags found across all derivation lines
    # (M1, v3-final).  Informational; not a pass/fail signal.
    assumed_tag_count: int = 0
    # True when ``assumed_tag_count`` exceeds the threshold; surfaces in the
    # stage log so the orchestrator / critic / UI can flag low-confidence
    # outputs without rejecting them outright.
    assumption_heavy: bool = False
    # Structural warnings from ``validate_s2_structure`` (e.g. missing
    # net_debt line, missing sbc_treatment, non-operator final line).
    # Empty list when validation was not requested or all checks passed.
    structure_warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.unjustified

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_numbers": self.total_numbers,
            "grounded_count": self.grounded_count,
            "declared_count": self.declared_count,
            "derived_count": self.derived_count,
            "passthrough_count": self.passthrough_count,
            "assumed_tag_count": self.assumed_tag_count,
            "assumption_heavy": self.assumption_heavy,
            "structure_warnings": list(self.structure_warnings),
            "unjustified": [asdict(u) for u in self.unjustified],
        }


# ----------------------------------------------------------- Extractors ---

def _to_dict(obj: Any) -> dict[str, Any]:
    """Normalise grounding to dict; accept dataclass or mapping."""
    if obj is None:
        return {}
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Unsupported grounding type: {type(obj).__name__}")


def _grounded_values(grounding: Any) -> dict[str, float]:
    """Map grounding field-name → numeric value (skip non-numeric)."""
    out: dict[str, float] = {}
    for k, v in _to_dict(grounding).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out[k] = float(v)
    return out


def _declared_assumptions(missing_inputs: Iterable[Any] | None) -> list[float]:
    """Extract numeric values from `ASSUMPTION:...=<value>` strings."""
    if not missing_inputs:
        return []
    vals: list[float] = []
    for entry in missing_inputs:
        if not isinstance(entry, str):
            continue
        for m in _ASSUMPTION_RE.finditer(entry):
            try:
                vals.append(float(m.group(1)))
            except ValueError:
                continue
    return vals


def _derived_values(node: Any) -> list[float]:
    """Walk node; for any list under key 'derivation', extract trailing
    `= <number>` from each string entry."""
    vals: list[float] = []

    def _walk(n: Any) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "derivation" and isinstance(v, list):
                    for line in v:
                        if isinstance(line, str):
                            m = _DERIVATION_FINAL_RE.search(line)
                            if m:
                                try:
                                    vals.append(float(m.group(1)))
                                except ValueError:
                                    pass
                else:
                    _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return vals


def _iter_numbers(node: Any, path: str = "") -> Iterator[tuple[str, float]]:
    """Yield (json-path, numeric value) for every numeric leaf."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        if not (isinstance(node, float) and math.isnan(node)):
            yield path, float(node)
        return
    if isinstance(node, dict):
        for k, v in node.items():
            sub = f"{path}.{k}" if path else k
            yield from _iter_numbers(v, sub)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            sub = f"{path}[{i}]"
            yield from _iter_numbers(item, sub)


# ------------------------------------------------------- Classification ---

def _is_passthrough(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1] if "." in path else path
    leaf = leaf.split("[", 1)[0]
    if leaf in _PASSTHROUGH_KEYS:
        return True
    if any(leaf.endswith(sfx) for sfx in _PASSTHROUGH_KEY_SUFFIXES):
        return True
    return any(hint in path for hint in _PASSTHROUGH_PATH_HINTS)


def _matches(value: float, candidate: float, tolerance: float) -> bool:
    if math.isclose(value, candidate, abs_tol=_TOLERANCE_ABS):
        return True
    if candidate == 0:
        return abs(value) < _TOLERANCE_ABS
    return abs(value - candidate) / abs(candidate) <= tolerance


def _nearest_grounded(
    value: float, grounded: dict[str, float]
) -> tuple[str | None, float | None, float | None]:
    """Return (field, scaled_value, distance%) for nearest grounded match
    under any allowed scaling."""
    best: tuple[str | None, float | None, float | None] = (None, None, None)
    best_dist = math.inf
    for name, raw in grounded.items():
        for factor in _SCALE_FACTORS:
            candidate = raw * factor
            if candidate == 0:
                continue
            d = abs(value - candidate) / abs(candidate)
            if d < best_dist:
                best_dist = d
                best = (name, candidate, d * 100.0)
    return best


# ----------------------------------------------------- Phase 2 helpers ---
# Provenance-tag accounting and S2 structural validation derived from the
# v3-final preamble rules (RULES A, C, H) injected by
# ``prompts.build_s2_system``.  These are *advisory* in Phase 2: warnings
# surface in the guard report and propagate to the stage log / critic
# prompt but do NOT flip ``GuardReport.passed`` on their own.

def _collect_derivation_lines(node: Any) -> list[str]:
    """Walk node; return every string entry found under a ``derivation`` key."""
    lines: list[str] = []

    def _walk(n: Any) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "derivation" and isinstance(v, list):
                    for line in v:
                        if isinstance(line, str):
                            lines.append(line)
                else:
                    _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return lines


def _count_assumed_tags(lines: Iterable[str]) -> int:
    """Count case-insensitive ``[ASSUMED]`` occurrences across derivation lines."""
    return sum(len(_ASSUMED_TAG_RE.findall(line)) for line in lines)


def _lhs(line: str) -> str:
    """Return the lower-cased symbol on the left of the first ``=``."""
    head = line.split("=", 1)[0]
    return head.strip().lower()


def _rhs_before_final(line: str) -> str:
    """Return everything between the first ``=`` and the trailing ``= <num>``.

    For ``"net_debt = 55 + 0 - 75 - 0 = -20"`` this is ``"55 + 0 - 75 - 0 "``.
    Used to count terms / operators on the bridge lines.
    """
    # Strip optional trailing tag.
    stripped = re.sub(r"\s*\[[^\]]*\]\s*$", "", line.rstrip())
    # Drop the final "= <number>".
    body = _DERIVATION_FINAL_RE.sub("", stripped)
    # Now body looks like "net_debt = 55 [from grounding] + 0 [from grounding] - ..."
    # Split on the first ``=`` and keep the RHS.
    parts = body.split("=", 1)
    return parts[1] if len(parts) == 2 else ""


def _has_operator(text: str) -> bool:
    return any(op in text for op in ("+", "-", "*", "/"))


def _count_numeric_tokens(text: str) -> int:
    """Count standalone numeric tokens in ``text`` (ignores numbers inside [tags])."""
    cleaned = re.sub(r"\[[^\]]*\]", " ", text)
    return len(re.findall(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", cleaned))


def validate_s2_structure(stage_output: dict[str, Any]) -> list[str]:
    """Run v3-final structural checks against an S2 output.

    Returns a list of human-readable warnings (empty == all checks passed).
    Checks performed:

    * **RULE A** — when any derivation line uses ``enterprise_value`` or
      ``fair_ev`` (i.e. an EV-based recipe), the canonical net_debt and
      equity_value bridge lines MUST be present with the full set of RHS
      terms.  Skipped for equity-direct models (P/E, DDM, NAV, rNPV).
    * **RULE C** — a literal ``sbc_treatment = "..."`` line MUST appear.
    * **RULE H** — the final ``fundamental = ... = <number>`` line MUST
      have at least one operator or a non-numeric symbol on the RHS.
    """
    warnings: list[str] = []
    lines = _collect_derivation_lines(stage_output)
    if not lines:
        return ["no derivation lines found in S2 output"]

    lhs_index: dict[str, str] = {}
    for line in lines:
        key = _lhs(line)
        # Last-write-wins is fine for our purposes; bridge / sbc / fundamental
        # lines should appear once each per recipe.
        if key:
            lhs_index[key] = line

    # ---- RULE A: EV-based recipes require the canonical bridge -----------
    uses_ev = any(
        "enterprise_value" in _lhs(line) or "fair_ev" in _lhs(line)
        for line in lines
    ) or any(
        "enterprise_value" in line.lower() or "fair_ev" in line.lower()
        for line in lines
    )
    if uses_ev:
        net_debt_line = lhs_index.get("net_debt")
        if not net_debt_line:
            warnings.append(
                "RULE A: EV-based recipe missing canonical 'net_debt = ...' line"
            )
        else:
            rhs = _rhs_before_final(net_debt_line)
            if _count_numeric_tokens(rhs) < 4:
                warnings.append(
                    "RULE A: net_debt bridge has < 4 RHS terms "
                    "(expected total_debt + capitalized_operating_leases - "
                    "cash_and_equivalents - short_term_investments)"
                )

        eq_line = lhs_index.get("equity_value")
        if not eq_line:
            warnings.append(
                "RULE A: EV-based recipe missing canonical 'equity_value = ...' line"
            )
        else:
            rhs = _rhs_before_final(eq_line)
            if _count_numeric_tokens(rhs) < 4:
                warnings.append(
                    "RULE A: equity_value bridge has < 4 RHS terms "
                    "(expected enterprise_value - net_debt - minority_interest "
                    "- preferred_equity - unfunded_pension_after_tax)"
                )

    # ---- RULE C: explicit SBC treatment --------------------------------
    if not any("sbc_treatment" in line.lower() for line in lines):
        warnings.append(
            'RULE C: missing literal \'sbc_treatment = "subtracted_from_fcf"\' '
            'or \'sbc_treatment = "kept_in_earnings_with_dilution"\' line'
        )

    # ---- RULE H: final-line discipline ---------------------------------
    fundamental_line = lhs_index.get("fundamental")
    if fundamental_line:
        rhs = _rhs_before_final(fundamental_line)
        rhs_clean = re.sub(r"\[[^\]]*\]", "", rhs).strip()
        if not rhs_clean:
            warnings.append(
                "RULE H: 'fundamental = <number>' has empty RHS expression "
                "(must derive via algebra or named symbols)"
            )
        elif not _has_operator(rhs_clean):
            # Allow a single named symbol (no operators) so long as it's not
            # purely a bare number.
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", rhs_clean):
                warnings.append(
                    "RULE H: final 'fundamental = ...' line is a bare number "
                    "with no operator or referenced symbol"
                )

    return warnings


# ----------------------------------------------------------- Public API ---

def guard(
    stage_output: dict[str, Any],
    grounding: Any,
    *,
    tolerance: float = _TOLERANCE_FRAC,
    extra_passthroughs: Iterable[str] = (),
    validate_structure: bool = False,
) -> GuardReport:
    """Classify every numeric leaf in ``stage_output``.

    Parameters
    ----------
    stage_output
        The JSON dict returned by an LLM stage.
    grounding
        Either an ``EtvGrounding`` dataclass or a plain dict of grounding
        fields. Numeric values are matched (with scaling) against these.
    tolerance
        Fractional tolerance for grounded / declared matches (default 0.5%).
    extra_passthroughs
        Additional leaf-key names to exempt from checks (e.g. a stage's
        own deterministic scalar fields).
    validate_structure
        When ``True`` (S2 callers), run :func:`validate_s2_structure` and
        populate ``report.structure_warnings``.  Advisory only — never
        flips ``report.passed``.
    """
    extra = frozenset(extra_passthroughs)
    grounded = _grounded_values(grounding)
    declared = _declared_assumptions(stage_output.get("missing_inputs"))
    derived = _derived_values(stage_output)
    deriv_lines = _collect_derivation_lines(stage_output)

    report = GuardReport()
    report.assumed_tag_count = _count_assumed_tags(deriv_lines)
    report.assumption_heavy = (
        report.assumed_tag_count > _ASSUMPTION_HEAVY_THRESHOLD
    )
    if validate_structure:
        report.structure_warnings = validate_s2_structure(stage_output)

    for path, value in _iter_numbers(stage_output):
        report.total_numbers += 1
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _is_passthrough(path) or leaf in extra:
            report.passthrough_count += 1
            continue

        # Grounded (with scaling) ?
        is_grounded = False
        for raw in grounded.values():
            for factor in _SCALE_FACTORS:
                if _matches(value, raw * factor, tolerance):
                    is_grounded = True
                    break
            if is_grounded:
                break
        if is_grounded:
            report.grounded_count += 1
            continue

        # Declared assumption ?
        if any(_matches(value, a, tolerance) for a in declared):
            report.declared_count += 1
            continue

        # Derived (LLM-claimed; spot-checked by critic stage later) ?
        if any(_matches(value, d, tolerance) for d in derived):
            report.derived_count += 1
            continue

        field_name, near_val, near_dist = _nearest_grounded(value, grounded)
        report.unjustified.append(
            Unjustified(
                path=path,
                value=value,
                nearest_grounded_field=field_name,
                nearest_grounded_value=near_val,
                nearest_distance_pct=near_dist,
            )
        )

    return report


def format_report_for_prompt(report: GuardReport, *, max_items: int = 8) -> str:
    """Render guard findings as a compact string to append to a retry prompt.

    Includes the unjustified-number block (when present), any structural
    warnings from :func:`validate_s2_structure` (advisory), and an
    assumption-heavy notice when the v3-final ``[ASSUMED]``-tag threshold
    is exceeded.
    """
    sections: list[str] = []
    if report.passed:
        sections.append("NUMERIC GUARD: PASSED (all numbers justified).")
    else:
        lines = [
            f"NUMERIC GUARD FAILED — {len(report.unjustified)} unjustified number(s):",
        ]
        for u in report.unjustified[:max_items]:
            suffix = ""
            if u.nearest_grounded_field and u.nearest_distance_pct is not None:
                suffix = (
                    f"  (nearest grounded: {u.nearest_grounded_field}"
                    f"={u.nearest_grounded_value:.4g}, off by"
                    f" {u.nearest_distance_pct:.1f}%)"
                )
            lines.append(f"  - {u.path} = {u.value:g}{suffix}")
        if len(report.unjustified) > max_items:
            lines.append(f"  ... and {len(report.unjustified) - max_items} more")
        lines.append(
            "Each number must be (a) a grounding value, (b) declared as "
            "ASSUMPTION:name=value in missing_inputs, or (c) derived in a "
            "derivation[] line. Revise to comply."
        )
        sections.append("\n".join(lines))

    if report.structure_warnings:
        wlines = ["STRUCTURE WARNINGS (advisory, v3-final preamble):"]
        for w in report.structure_warnings:
            wlines.append(f"  - {w}")
        sections.append("\n".join(wlines))

    if report.assumption_heavy:
        sections.append(
            f"ASSUMPTION-HEAVY OUTPUT: {report.assumed_tag_count} [ASSUMED] "
            f"tags across derivations (threshold = {_ASSUMPTION_HEAVY_THRESHOLD}). "
            "Prefer grounding values or derived expressions over assumptions "
            "where possible."
        )

    return "\n\n".join(sections)
