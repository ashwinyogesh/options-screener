"""Deterministic post-validator for the ETV report.

Enforces arithmetic and identity invariants the LLM cannot be trusted
with: probability normalisation, scenario-price identity, intrinsic-vs-
tradable separation, weighted ETV, asymmetry, decision gates.
"""
from __future__ import annotations

from .iv_prior import compute_posterior

_DECOMP_KEYS = (
    "fundamental",
    "regime_adjustment",
    "market_expectations_adjustment",
    "optionality",
    "behavioral_premium",
)


def validate_report(
    report: dict,
    spot: float | None,
    *,
    iv_annual: float | None = None,
    horizon_days: int | None = None,
) -> dict:
    """Deterministic post-validation.

    Enforces:
      * scenario probabilities sum to 100 (normalised)
      * each scenario `price` equals Σ(value_decomposition) within ±$1
      * `etv.probability_weighted_etv` = Σ(p_s × price_s) / 100
      * `economic_value.central_estimate` = same weighted sum
      * `etv.expected_return_pct` = (ETV − spot) / spot × 100
      * `asymmetry.ratio` = |weighted upside %| / |weighted downside %|
      * `decision.confidence_pct` ≤ 90
      * Decision = NO TRADE if asymmetry < 2 OR confidence < 55

    When ``iv_annual`` and ``horizon_days`` are provided, scenario
    probabilities are computed as a **Bayesian posterior**: lognormal
    IV prior over the LLM's scenario prices, multiplied element-wise
    by the LLM's clamped likelihood ratios, and renormalised.  This
    replaces the LLM's raw ``probability_pct`` for the asymmetry gate
    while keeping the original values surfaced under
    ``validation.probability_check.llm_pct`` for diagnostics.

    Emits a `validation` block with corrections applied and warnings
    raised.  Mutates `report` in place; returns it.
    """
    warnings: list[str] = []
    corrections: list[str] = []
    prob_check: dict | None = None

    def _fix_scenarios(block_name: str) -> tuple[float, dict, dict, dict]:
        block = report.get(block_name) or {}
        scns = {s: (block.get(s) or {}) for s in ("bear", "base", "bull")}
        # Probability normalisation
        probs = {s: float(scns[s].get("probability_pct") or 0) for s in scns}
        total = sum(probs.values())
        if total <= 0:
            warnings.append(f"{block_name}: all probabilities zero/missing")
            return 0.0, *scns.values()  # type: ignore[return-value]
        if abs(total - 100) > 0.5:
            for s in scns:
                scns[s]["probability_pct"] = round(probs[s] * 100.0 / total, 1)
            corrections.append(
                f"{block_name}: probabilities normalised from {total:.1f} to 100"
            )
            probs = {s: scns[s]["probability_pct"] for s in scns}
        # Weighted price (identity enforcement below will refine prices)
        wprice = sum(probs[s] * float(scns[s].get("price") or 0) for s in scns) / 100.0
        return wprice, scns["bear"], scns["base"], scns["bull"]

    # Economic-value block — probabilities only at this stage
    _econ_w_pre, _eb, _ebase, _ebull = _fix_scenarios("economic_value")
    econ_block = report.get("economic_value") or {}

    # ETV block — probabilities only
    _etv_w_pre, eb, ebase, ebull = _fix_scenarios("etv")
    etv_block = report.get("etv") or {}

    # Enforce identity: economic_value = intrinsic (fundamental only),
    #                   etv = fundamental + 4 layered components
    econ_scns = {"bear": _eb, "base": _ebase, "bull": _ebull}
    etv_scns = {"bear": eb, "base": ebase, "bull": ebull}
    for s in ("bear", "base", "bull"):
        ev = econ_scns[s]
        et = etv_scns[s]
        ev_d = ev.get("value_decomposition") or {}
        et_d = et.get("value_decomposition") or {}
        # 1. economic_value = STRICT intrinsic (fundamental only).
        #    Zero out the other four components; set price = fundamental.
        if ev_d:
            zeroed: list[str] = []
            for k in ("regime_adjustment", "market_expectations_adjustment",
                      "optionality", "behavioral_premium"):
                v = ev_d.get(k)
                if v is not None and abs(float(v)) > 0.5:
                    zeroed.append(f"{k}={float(v):+.0f}")
                ev_d[k] = 0
            fund = float(ev_d.get("fundamental") or 0)
            old_ev_price = ev.get("price")
            if old_ev_price is None or abs(float(old_ev_price) - fund) > 1:
                ev["price"] = round(fund)
                corrections.append(
                    f"economic_value.{s}.price: {old_ev_price} → ${ev['price']} (= fundamental)"
                )
            if zeroed:
                corrections.append(
                    f"economic_value.{s}: zeroed non-fundamental components ({', '.join(zeroed)})"
                )
            ev["value_decomposition"] = ev_d
        # 2. Force ETV.fundamental to match economic_value.fundamental
        ev_fund = ev_d.get("fundamental") if ev_d else None
        if ev_fund is not None:
            et_fund = et_d.get("fundamental")
            if et_fund is None or abs(float(et_fund) - float(ev_fund)) > 0.5:
                et_d["fundamental"] = ev_fund
                corrections.append(
                    f"etv.{s}.fundamental: {et_fund} → {ev_fund} (match economic intrinsic)"
                )
        # 3. Recompute ETV price = fundamental + the 4 layered components
        if et_d:
            new_etv_price = sum(float(et_d.get(k) or 0) for k in _DECOMP_KEYS)
            old_etv_price = et.get("price")
            if old_etv_price is None or abs(float(old_etv_price) - new_etv_price) > 1:
                et["price"] = round(new_etv_price)
                corrections.append(
                    f"etv.{s}.price: {old_etv_price} → ${et['price']} (= Σ decomposition)"
                )
            et["value_decomposition"] = et_d
        # 4. Force matching probabilities (econ wins — it's the structural anchor)
        if ev.get("probability_pct") is not None and \
                et.get("probability_pct") != ev.get("probability_pct"):
            old_p = et.get("probability_pct")
            et["probability_pct"] = ev["probability_pct"]
            corrections.append(
                f"etv.{s}.probability_pct: {old_p} → {ev['probability_pct']} (match econ)"
            )

    # ----- IV-implied Bayesian posterior (Option 1 + Option 4) -----------
    # Replace the LLM's hand-picked probabilities with a posterior built
    # from a lognormal cone over the (now-final) scenario prices and the
    # LLM's clamped likelihood ratios.  Falls back silently to the LLM's
    # ``probability_pct`` when iv30 / horizon are missing.
    posterior = compute_posterior(
        spot=spot,
        iv_annual=iv_annual,
        horizon_days=horizon_days,
        scenarios={
            "bear": econ_scns["bear"], "base": econ_scns["base"],
            "bull": econ_scns["bull"],
        },
    )
    if posterior is not None:
        llm_pct = {
            s: float(econ_scns[s].get("probability_pct") or 0)
            for s in ("bear", "base", "bull")
        }
        for s in ("bear", "base", "bull"):
            new_pct = round(posterior["posterior"][s] * 100.0, 1)
            old_pct = econ_scns[s].get("probability_pct")
            econ_scns[s]["probability_pct"] = new_pct
            etv_scns[s]["probability_pct"] = new_pct
            corrections.append(
                f"{s}.probability_pct: {old_pct} → {new_pct} (IV-posterior)"
            )
        prob_check = {
            "method": "iv_posterior",
            "iv_annual": posterior["iv_annual"],
            "horizon_days": posterior["horizon_days"],
            "lr_provided": posterior["lr_provided"],
            "prior_pct": {k: round(v * 100.0, 1)
                          for k, v in posterior["prior"].items()},
            "lr_llm": posterior["lr_llm"],
            "lr_clamped": posterior["lr_clamped"],
            "posterior_pct": {k: round(v * 100.0, 1)
                              for k, v in posterior["posterior"].items()},
            "llm_pct": llm_pct,
        }
    else:
        if iv_annual is None or horizon_days is None:
            prob_check = {"method": "llm_only",
                          "reason": "iv_annual or horizon_days not provided"}
        else:
            prob_check = {"method": "llm_only",
                          "reason": "invalid scenario prices or grounding"}

    # Recompute weighted sums AFTER identity enforcement
    etv_w = sum(float(etv_scns[s].get("probability_pct") or 0)
                * float(etv_scns[s].get("price") or 0)
                for s in etv_scns) / 100.0
    econ_w = sum(float(econ_scns[s].get("probability_pct") or 0)
                 * float(econ_scns[s].get("price") or 0)
                 for s in econ_scns) / 100.0
    if econ_w:
        econ_block["central_estimate"] = round(econ_w)

    if etv_w:
        old = etv_block.get("probability_weighted_etv")
        new = round(etv_w, 2)
        if old is None or abs(float(old) - new) > 0.5:
            etv_block["probability_weighted_etv"] = new
            corrections.append(
                f"etv.probability_weighted_etv: {old} → {new} (weighted)"
            )
        # Aggregate decomposition (probability-weighted components)
        agg = {k: 0.0 for k in _DECOMP_KEYS}
        any_present = False
        for s, sc in (("bear", eb), ("base", ebase), ("bull", ebull)):
            decomp = sc.get("value_decomposition") or {}
            p = float(sc.get("probability_pct") or 0) / 100.0
            for k in _DECOMP_KEYS:
                v = decomp.get(k)
                if v is not None:
                    any_present = True
                    agg[k] += p * float(v)
        if any_present:
            etv_block["weighted_decomposition"] = {k: round(v, 2) for k, v in agg.items()}
            etv_block["weighted_decomposition_sum"] = round(sum(agg.values()), 2)
        # Expected return
        if spot and spot > 0:
            er = (new - spot) / spot * 100.0
            old_er = etv_block.get("expected_return_pct")
            if old_er is None or abs(float(old_er) - er) > 0.3:
                etv_block["expected_return_pct"] = round(er, 2)
                corrections.append(
                    f"etv.expected_return_pct: {old_er} → {round(er, 2)}"
                )
            etv_block["current_price"] = spot

    # Asymmetry
    asym_block = report.get("asymmetry") or {}
    if spot and spot > 0 and etv_w:
        up = 0.0
        dn = 0.0
        for s, sc in (("bear", eb), ("base", ebase), ("bull", ebull)):
            p = float(sc.get("probability_pct") or 0) / 100.0
            px = float(sc.get("price") or 0)
            ret = (px - spot) / spot * 100.0
            if ret >= 0:
                up += p * ret
            else:
                dn += p * abs(ret)
        ratio = (up / dn) if dn > 1e-6 else float("inf")
        asym_block["upside_pct_weighted"] = round(up, 2)
        asym_block["downside_pct_weighted"] = round(dn, 2)
        asym_block["ratio"] = round(ratio, 2) if ratio != float("inf") else None
        corrections.append(
            f"asymmetry: upside={up:.1f}%, downside={dn:.1f}%, ratio={ratio:.2f}"
        )

        # ----- Counterfactual ratios for the probability_check block -----
        # ratio_llm: what the ratio WOULD have been with the LLM's raw
        # probability_pct (before IV-posterior overwrite).
        # ratio_prior: what the ratio WOULD have been under the pure IV
        # lognormal cone (LR == 1 for every scenario).
        if prob_check and prob_check.get("method") == "iv_posterior":
            def _ratio_from_pct(pct_by_label: dict) -> float | None:
                up_x = 0.0
                dn_x = 0.0
                for s, sc in (("bear", eb), ("base", ebase), ("bull", ebull)):
                    p_x = float(pct_by_label.get(s) or 0) / 100.0
                    px_x = float(sc.get("price") or 0)
                    ret_x = (px_x - spot) / spot * 100.0
                    if ret_x >= 0:
                        up_x += p_x * ret_x
                    else:
                        dn_x += p_x * abs(ret_x)
                if dn_x <= 1e-6:
                    return None
                return round(up_x / dn_x, 2)

            r_llm = _ratio_from_pct(prob_check.get("llm_pct", {}))
            r_prior = _ratio_from_pct(prob_check.get("prior_pct", {}))
            r_post = asym_block.get("ratio")
            prob_check["ratio_llm"] = r_llm
            prob_check["ratio_prior"] = r_prior
            prob_check["ratio_posterior"] = r_post
            # Decision under pure IV prior (LR=1).  Used to flag trades
            # whose only support comes from the LLM's LR view.
            prob_check["decision_under_prior"] = (
                "TRADE" if isinstance(r_prior, (int, float)) and r_prior >= 2.0
                else "NO TRADE"
            )
            prob_check["decision_under_posterior"] = (
                "TRADE" if isinstance(r_post, (int, float)) and r_post >= 2.0
                else "NO TRADE"
            )
            prob_check["decision_relies_on_llm_view"] = (
                prob_check["decision_under_prior"]
                != prob_check["decision_under_posterior"]
            )
            # Gap between LLM-raw and posterior ratios → fragility flag.
            if isinstance(r_llm, (int, float)) and isinstance(r_post, (int, float)):
                prob_check["ratio_gap_llm_vs_posterior"] = round(
                    abs(r_llm - r_post), 2
                )
                prob_check["decision_fragile"] = (
                    abs(r_llm - r_post) > 0.5
                )
            else:
                prob_check["decision_fragile"] = False

    # Decision rule enforcement
    dec_block = report.get("decision") or {}
    conf = float(dec_block.get("confidence_pct") or 0)
    if conf > 90:
        dec_block["confidence_pct"] = 90
        corrections.append(f"decision.confidence_pct: {conf} → 90 (cap)")
        conf = 90
    # Capture the LLM's pre-guard "thesis confidence" (after enforcing the
    # declared 0–90 ceiling, before deterministic gate penalties).  Surfaced
    # separately from the final number so the UI can tell a genuinely weak
    # thesis apart from a strong thesis that a server guard knocked down.
    thesis_conf = conf
    gate_adjustments: list[dict] = []
    # Fragility deduction: when the LLM's LRs swung the ratio by > 0.5
    # away from the IV-prior decision, dock 15 from confidence.
    if prob_check and prob_check.get("decision_fragile"):
        deduction = 15
        new_conf = max(0.0, conf - deduction)
        if new_conf != conf:
            dec_block["confidence_pct"] = new_conf
            deductions = list(dec_block.get("confidence_deductions") or [])
            deductions.append(
                f"-{deduction}: posterior-vs-llm ratio gap "
                f"{prob_check.get('ratio_gap_llm_vs_posterior')} > 0.5"
            )
            dec_block["confidence_deductions"] = deductions
            corrections.append(
                f"decision.confidence_pct: {conf} → {new_conf} (LR-fragility)"
            )
            gate_adjustments.append({
                "source": "lr_fragility",
                "delta": round(new_conf - conf, 1),
                "reason": (
                    f"posterior-vs-llm ratio gap "
                    f"{prob_check.get('ratio_gap_llm_vs_posterior')} > 0.5"
                ),
            })
            conf = new_conf
    # Anti-anchoring guard: base-case intrinsic within 5% of spot
    # strongly suggests the LLM rediscovered spot via a current-company
    # multiple.  Flag intrinsic.anchored_to_spot and clamp confidence
    # to 60.  See _S2_GLOBAL_RULES section I.
    if spot and spot > 0:
        base_fund = float((ebase or {}).get("fundamental") or 0)
        if base_fund > 0:
            spot_gap = abs(base_fund - spot) / spot
            if spot_gap < 0.05:
                report.setdefault("intrinsic", {})["anchored_to_spot"] = True
                report["intrinsic"]["anchored_to_spot_gap_pct"] = round(
                    spot_gap * 100, 2
                )
                if conf > 60:
                    new_conf = 60.0
                    deductions = list(dec_block.get("confidence_deductions") or [])
                    deductions.append(
                        f"clamp_to_60: base intrinsic ${base_fund:.2f} within "
                        f"{spot_gap*100:.1f}% of spot ${spot:.2f} "
                        f"— anchoring suspected"
                    )
                    dec_block["confidence_deductions"] = deductions
                    dec_block["confidence_pct"] = new_conf
                    corrections.append(
                        f"decision.confidence_pct: {conf} → {new_conf} "
                        f"(anchored-to-spot)"
                    )
                    gate_adjustments.append({
                        "source": "anchored_to_spot",
                        "delta": round(new_conf - conf, 1),
                        "reason": (
                            f"base intrinsic ${base_fund:.2f} within "
                            f"{spot_gap*100:.1f}% of spot ${spot:.2f}"
                        ),
                    })
                    conf = new_conf
    # Surface the thesis/gate split on the decision block.  thesis_conf is
    # the LLM's pre-guard number; gate_adjustments lists each deterministic
    # penalty (negative deltas).  Final confidence_pct = thesis_conf +
    # Σ(gate_adjustments deltas).
    dec_block["thesis_confidence_pct"] = round(thesis_conf, 1)
    dec_block["gate_adjustments"] = gate_adjustments
    ratio = asym_block.get("ratio")
    no_trade_reasons: list[str] = []
    if isinstance(ratio, (int, float)) and ratio < 2:
        no_trade_reasons.append(f"asymmetry {ratio:.2f} < 2")
    if conf < 55:
        no_trade_reasons.append(f"confidence {conf:.0f} < 55")
    if no_trade_reasons and dec_block.get("decision") == "TRADE":
        dec_block["decision"] = "NO TRADE"
        dec_block["direction"] = "NEUTRAL"
        corrections.append("decision: TRADE → NO TRADE (" + "; ".join(no_trade_reasons) + ")")

    report["validation"] = {
        "warnings": warnings,
        "corrections": corrections,
        "passed": len(warnings) == 0,
    }
    if prob_check is not None:
        report["validation"]["probability_check"] = prob_check
    return report
