"""Rules engine: run every deterministic check, emit candidate findings with default judgements.

Used three ways: (1) evidence pack handed to Muse agents, (2) zero-credit fallback when LLM is off,
(3) ablation baseline in eval.
"""
from __future__ import annotations
from core.schemas import Finding, Citation
from core.tools import checks as C
from core.tools.payout import compute_payout
from core import kb
from core.fmt import money


def _m(b: C.Bundle, x):
    p = b.pack
    return money(x, p.get("currency", ""), p.get("number_format", "western"))


def _pcite(ref) -> Citation | None:
    if ref is not None and not isinstance(ref, dict):
        ref = ref.model_dump() if hasattr(ref, "model_dump") else {}
    if not ref or not (ref.get("quote") or ref.get("clause_id")):
        return None
    return Citation(doc="policy", page=ref.get("page"), quote=ref.get("quote"), bbox=None)


def _rcite(b: C.Bundle, rule_id: str | None) -> Citation | None:
    if not rule_id:
        return None
    r = kb.rule(b.ctx.jurisdiction, rule_id)
    if not r:
        return None
    return Citation(doc=f"{r['source']} [{r['id']}]", page=r.get("page"), quote=r.get("quote") or r["rule"],
                    verified=bool(r.get("quote") and not r.get("seed")))


def _bcite(b: C.Bundle, item_ids: list[str]) -> Citation | None:
    it = next((i for i in b.bill.line_items if i.id in item_ids), None)
    if not it:
        return None
    return Citation(doc="bill", page=it.page, quote=it.description)


def evidence_pack(b: C.Bundle) -> dict:
    """Everything the tools know — compact JSON for agents."""
    return {
        "reconcile": C.reconcile_totals(b), "non_payables": C.non_payable_check(b),
        "room": C.room_rent_check(b, "room"), "icu": C.room_rent_check(b, "icu"),
        "proportionate": C.proportionate_check(b), "waiting": C.waiting_period_check(b), "ped": C.ped_check(b),
        "sublimit": C.sublimit_check(b), "copay": C.copay_check(b), "docs": C.required_docs(b),
        "intimation": C.intimation_check(b), "exclusions": C.exclusion_check(b), "tariff": C.tariff_benchmark(b),
        "rejection_reasons": C.rejection_reasons(b),
    }


def detect(b: C.Bundle, ev: dict | None = None) -> list[Finding]:
    ev = ev or evidence_pack(b)
    mode = b.ctx.mode
    appeal = mode == "appeal"
    base = compute_payout(b)
    dmap = {d.id: d for d in base.deductions}
    F: list[Finding] = []

    def add(**kw):
        kw.setdefault("raised_by", "rules")
        F.append(Finding(id=f"F{len(F) + 1:02d}", **kw))

    pol = b.policy
    if pol.room_rent.kind == "none" and not pol.waiting_periods and not pol.copays and not pol.exclusions and not pol.sublimits:
        add(issue_type="other", title="Policy wording could not be read", status="needs_review", inr_impact=0, confidence=0.5,
            explanation_en="No limits, waiting periods or exclusions could be read from this policy (it may be a scan or a schedule page only). "
                           "Bill-level checks (non-payables, totals, duplicates, documents) still apply.",
            fix="Upload the full policy wording PDF (not the schedule/e-card), or pick the product from the library.")
    rec = ev["reconcile"]
    if "D-UNITEM" in dmap:
        d = dmap["D-UNITEM"]
        add(issue_type="mismatch", title="Bill total does not match itemized charges", status="fixable", inr_impact=d.amount,
            deduction_ids=["D-UNITEM"], confidence=0.95,
            explanation_en=f"The bill total {_m(b, rec['stated_total'])} is {_m(b, d.amount)} more than the sum of its itemized lines "
                           f"({_m(b, rec['net_of_discount'])}). Insurers only pay itemized charges, so this gap would be deducted.",
            fix=f"Ask the billing desk for a fully itemized final bill that explains the {_m(b, d.amount)} difference.",
            bill_cite=Citation(doc="bill", page=1, quote="Total"), tool_evidence={"reconcile_totals": {k: rec[k] for k in ("line_sum", "stated_total", "unitemized_diff")}})
    for did, title, key in (("D-DUP", "Duplicate charges on the bill", "duplicates"), ("D-ARITH", "Quantity × rate errors", "arithmetic_errors")):
        if did in dmap:
            d = dmap[did]
            add(issue_type="mismatch", title=title, status="fixable", inr_impact=d.amount, deduction_ids=[did], confidence=0.95,
                explanation_en=f"{len(rec[key])} line(s) look billed in error, worth {_m(b, d.amount)}. The insurer will not pay them and you would owe them to the hospital.",
                fix="Ask the hospital to correct these lines before you pay or submit.", bill_cite=_bcite(b, d.item_ids),
                tool_evidence={key: rec[key]})

    np = ev["non_payables"]
    if "D-NP" in dmap:
        d = dmap["D-NP"]
        items = [m for m in np["matches"] if not m["unitemized_kit"]]
        subsumed = sum(m["amount"] for m in items if m["list"] != "I")
        add(issue_type="non_payable", title=f"{len(items)} non-payable item(s) on the bill", status="valid", inr_impact=d.amount,
            deduction_ids=["D-NP"], confidence=0.9,
            explanation_en="These items are on the regulator's standard non-payable lists: " +
                           ", ".join(f"{m['description']} ({_m(b, m['amount'])}, List {m['list']})" for m in items[:8]) + ".",
            fix=(f"Ask the hospital to drop the {_m(b, subsumed)} of items that are meant to be included in room/procedure charges (Lists II–IV)."
                 if subsumed > 0 else "Expect to pay these yourself; they are excluded by regulation."),
            bill_cite=_bcite(b, d.item_ids), irdai_cite=_rcite(b, "IN-NONPAY-LISTS"), tool_evidence={"non_payable_check": items})
    if "D-NP-KIT" in dmap:
        d = dmap["D-NP-KIT"]
        add(issue_type="non_payable", title="'Kit' charges without itemization", status="fixable", inr_impact=d.amount,
            deduction_ids=["D-NP-KIT"], confidence=0.85,
            explanation_en=f"A kit billed as one line ({_m(b, d.amount)}) with no contents is disallowed as 'any kit with no details mentioned'. Most of its contents are usually payable consumables.",
            fix="Ask for an itemized breakup of the kit (each consumable with quantity and price).",
            bill_cite=_bcite(b, d.item_ids), irdai_cite=_rcite(b, "IN-NONPAY-LISTS"))

    rr = ev["room"]
    if "D-ROOM" in dmap:
        d = dmap["D-ROOM"]
        add(issue_type="room_rent", title="Room rent above your policy limit", status="valid", inr_impact=d.amount, deduction_ids=["D-ROOM"],
            confidence=0.95,
            explanation_en=f"Your policy allows {_m(b, rr['cap_per_day'])}/day; the room was billed at {_m(b, rr['charged_per_day'])}/day for {rr['days']:g} day(s).",
            fix=("If you are still admitted, move to a room within the limit for the remaining days." if mode == "pre_discharge"
                 else "This part is a valid deduction; choose an eligible room next time."),
            policy_cite=_pcite(rr["cite"]), bill_cite=_bcite(b, d.item_ids), tool_evidence={"room_rent_check": rr})
    if "D-ICU" in dmap:
        d = dmap["D-ICU"]; ic = ev["icu"]
        add(issue_type="icu_rent", title="ICU charges above limit", status="valid", inr_impact=d.amount, deduction_ids=["D-ICU"],
            explanation_en=f"ICU limit {_m(b, ic['cap_per_day'])}/day vs billed {_m(b, ic['charged_per_day'])}/day.",
            policy_cite=_pcite(ic["cite"]), bill_cite=_bcite(b, d.item_ids))
    if b.policy.room_rent.kind == "pct_si" and not b.si and any(i.category == "room" for i in b.bill.line_items):
        add(issue_type="room_rent", title="Sum insured needed to check the room-rent limit", status="needs_review", inr_impact=0, confidence=0.6,
            explanation_en=f"Your policy caps room rent at {b.policy.room_rent.value:g}% of the sum insured, but the sum insured is not in the wording.",
            fix="Enter the sum insured from your policy card / schedule and re-run the check.", policy_cite=_pcite(b.policy.room_rent.cite))
    if rr.get("category_above_entitlement"):
        add(issue_type="room_rent", title="Room category above your entitlement", status="needs_review", inr_impact=0, confidence=0.7,
            explanation_en=f"Your policy allows “{rr['category_limit']}”, but the bill shows “{b.bill.room_category}”. The insurer may scale doctor, OT and "
                           "nursing charges down to the eligible room's tariff.",
            fix="Ask the hospital for its tariff of the eligible room category, and whether doctor/OT fees differ by room (if not, no proportionate cut applies).",
            policy_cite=_pcite(rr["cite"]), irdai_cite=_rcite(b, "IN-PD-NODIFF"), tool_evidence={"room_rent_check": rr})
    pd = ev["proportionate"]
    if "D-PD" in dmap:
        d = dmap["D-PD"]
        if pd["differential_billing"] is False:
            st, fix, rule = "contestable", "Cite the regulator rule: no proportionate deduction where the hospital does not bill differently by room category.", "IN-PD-NODIFF"
        elif appeal:
            st, fix, rule = "contestable", "Obtain a hospital certificate that surgeon/OT fees do not vary by room category and appeal.", "IN-PD-NODIFF"
        else:
            st, fix, rule = "fixable", "Get a hospital certificate that surgeon, OT and nursing fees are the same in every room category (no differential billing).", "IN-PD-NODIFF"
        add(issue_type="proportionate", title="Proportionate deduction on doctor/OT/nursing fees", status=st, inr_impact=d.amount,
            deduction_ids=["D-PD"], confidence=0.85,
            explanation_en=(f"Because the room exceeded the limit, the insurer scales associated charges ({_m(b, pd['associated_total'])}) by "
                            f"{pd['ratio']:.0%}, cutting {_m(b, d.amount)}. Pharmacy, consumables, implants and diagnostics "
                            f"({_m(b, pd['exempt_total'])}) are protected from this cut."),
            fix=fix, policy_cite=_pcite(pd["cite"]), irdai_cite=_rcite(b, rule), tool_evidence={"proportionate_check": pd})

    wp = ev["waiting"]
    for h in wp.get("hits", []):
        if not h["applies_insurer_view"] and not h["applies_correctly"]:
            continue
        if h["applies_insurer_view"] and not h["applies_correctly"]:
            via_reg = bool(h["regulator_max"] and h["months_required"] > h["regulator_max"] and h["months_elapsed_insurer_view"] >= h["regulator_max"])
            st = "contestable" if (appeal or via_reg) else "fixable"
            fix = ("Submit previous policy copies proving continuous cover; waiting periods already served must be credited."
                   if not via_reg else "Cite the regulator cap on waiting periods.")
            rule = "IN-SPECIFIC-MAX" if via_reg else "IN-PORTABILITY"
        else:
            st, fix, rule = "valid", "Claim is likely inadmissible until the waiting period ends; check if treatment can be deferred or is an emergency/accident.", None
        add(issue_type="waiting_period", title=f"{h['kind'].title()} waiting period: {h['condition']}", status=st,
            inr_impact=dmap["D-WAIT"].amount if "D-WAIT" in dmap else 0, deduction_ids=["D-WAIT"] if "D-WAIT" in dmap else [],
            confidence=0.9,
            explanation_en=f"The policy requires {h['months_required']} months for {h['condition']}. Covered {h['months_elapsed_insurer_view']} months with this insurer"
                           + (f", {h['months_elapsed_continuous']} months continuously including earlier policies." if h['months_elapsed_continuous'] != h['months_elapsed_insurer_view'] else "."),
            fix=fix, policy_cite=_pcite(h["cite"]), irdai_cite=_rcite(b, rule), tool_evidence={"waiting_period_check": h})

    ped = ev["ped"]
    if not appeal and ped["undeclared"] and not ped["moratorium_passed"]:
        add(issue_type="ped", title="Possible undisclosed pre-existing condition", status="needs_review", inr_impact=base.payout,
            confidence=0.6,
            explanation_en=f"Records mention {', '.join(ped['undeclared'])}, not declared on the proposal. If it pre-dates the policy, the insurer may repudiate.",
            fix="Check what was declared at purchase. If disclosed, keep the proposal form ready; if diagnosed after the policy start, get a doctor's certificate of first diagnosis.",
            policy_cite=_pcite(ped["cite"]), tool_evidence={"ped_check": ped})

    if "D-SUBLIMIT" in dmap:
        d = dmap["D-SUBLIMIT"]; sl = ev["sublimit"]
        add(issue_type="sublimit", title=f"Sub-limit for {sl['procedure']}", status="valid", inr_impact=d.amount, deduction_ids=["D-SUBLIMIT"],
            explanation_en=f"Your policy caps {sl['procedure']} at {_m(b, sl['limit'])}; the admissible amount exceeds it by {_m(b, d.amount)}.",
            fix="Valid policy limit. Confirm the procedure was correctly classified.", policy_cite=_pcite(sl["cite"]), tool_evidence={"sublimit_check": sl})
    cp = ev["copay"]
    if cp["pct"] and base.copay:
        add(issue_type="copay", title=f"{cp['pct']:g}% co-payment", status="valid", inr_impact=base.copay,
            explanation_en=f"Your policy requires you to pay {cp['pct']:g}% of the admissible amount ({cp['condition']}).",
            policy_cite=_pcite(cp["cite"]), tool_evidence={"copay_check": cp})

    miss = ev["docs"]["missing"]
    if miss:
        crit = [m for m in miss if m["critical"]]
        add(issue_type="missing_doc", title=f"{len(miss)} document(s) missing", status="fixable", inr_impact=0, confidence=0.9,
            explanation_en="Missing: " + ", ".join(m["label"] for m in miss) + (". Critical documents missing — the claim will be queried or held." if crit else "."),
            fix="Collect these before discharge/submission; the hospital must provide medical records on request.",
            tool_evidence={"required_docs": ev["docs"]})
    it = ev["intimation"]
    if it.get("late"):
        add(issue_type="intimation", title="Late intimation to insurer", status="contestable", inr_impact=0,
            explanation_en=f"Intimated {it['hours']:g}h after admission vs {it['limit_hours']}h in policy. Delay alone should not defeat a genuine claim.",
            fix="Explain the reason for delay in writing.", irdai_cite=_rcite(b, "IN-CONDONE-DELAY"))
    for e in ev["exclusions"]["hits"]:
        add(issue_type="exclusion", title=f"Possible exclusion: {e['code'] or e['matched']}", status="needs_review", inr_impact=0, confidence=0.5,
            explanation_en=f"Treatment may fall under exclusion: {e['text'][:200]}", policy_cite=_pcite(e["cite"]))

    # appeal mode: judge each insurer reason
    for r in ev["rejection_reasons"]:
        if r["issue_type"] == "ped":
            if ped["moratorium_passed"]:
                st, rule, why = "contestable", "IN-MORATORIUM", f"Cover has been continuous for {ped['months_continuous']} months, beyond the {ped['moratorium_months']}-month moratorium. Non-disclosure cannot be raised without proof of fraud."
            elif ped["ped_waiting_served"]:
                st, rule, why = "contestable", "IN-PED-MAX", f"Even if pre-existing, the PED waiting period was served ({ped['months_continuous']} months)."
            elif ped["condition_predates_policy"] is False:
                st, rule, why = "contestable", None, "Records show the condition was diagnosed after the policy started."
            else:
                st, rule, why = "needs_review", None, "Could not establish when the condition began."
            add(issue_type="wrongful_rejection", title="Rejection for pre-existing disease / non-disclosure", status=st,
                inr_impact=dmap["D-PED"].amount if "D-PED" in dmap else 0, deduction_ids=["D-PED"] if "D-PED" in dmap else [],
                confidence=0.9 if st == "contestable" else 0.5, explanation_en=f"Insurer: “{r['text']}”. {why}",
                fix="File an appeal citing the rule below, then escalate to the Ombudsman if not resolved.",
                irdai_cite=_rcite(b, rule), tool_evidence={"ped_check": ped, "reason": r})
        elif r["issue_type"] in ("other", "exclusion") and b.rejection and b.rejection.decision == "partial":
            continue                                   # a partial cut is judged as a whole by the short-settlement check below
        elif r["issue_type"] in ("missing_doc", "intimation", "other", "exclusion"):
            rej_ids = ["D-REJ"] if "D-REJ" in dmap else []
            st = {"missing_doc": "fixable", "intimation": "contestable"}.get(r["issue_type"], "needs_review")
            rule = {"missing_doc": "IN-CRC", "intimation": "IN-CONDONE-DELAY"}.get(r["issue_type"], "IN-CRC")
            why = {"missing_doc": "Submit the documents with the appeal; a claim cannot be closed for documents the insurer never specifically requested.",
                   "intimation": "Delay in intimation alone should not defeat a genuine, documented hospitalisation."}.get(r["issue_type"], "Ask the insurer for the exact clause relied on.")
            add(issue_type="wrongful_rejection" if r["issue_type"] != "missing_doc" else "missing_doc", title=f"Insurer reason: {r['text'][:60]}",
                status=st, inr_impact=dmap["D-REJ"].amount if rej_ids else 0, deduction_ids=rej_ids, confidence=0.8,
                explanation_en=f"Insurer: “{r['text']}”. {why}", fix="Reply in writing with the explanation/documents and cite the rule below.",
                irdai_cite=_rcite(b, rule), tool_evidence={"reason": r})
    rej = b.rejection
    if appeal and rej and rej.decision == "partial" and rej.amount_paid is not None and base.payout - rej.amount_paid > 0.5:
        kinds = {r["issue_type"] for r in ev["rejection_reasons"]}
        rule = "IN-PD-EXEMPT" if "proportionate" in kinds else "IN-NONPAY-LISTS" if "non_payable" in kinds else "IN-CRC"
        gap = base.payout - rej.amount_paid
        add(issue_type="short_settlement", title="Insurer paid less than its own policy terms allow", status="contestable", inr_impact=gap, confidence=0.9,
            explanation_en=(f"Even applying every valid deduction in the policy, the correct settlement is {_m(b, base.payout)}; the insurer paid "
                            f"{_m(b, rej.amount_paid)}. The extra {_m(b, gap)} cut is not supported"
                            + (" — proportionate deduction was applied to pharmacy, consumables, implants or diagnostics, which is not allowed." if rule == "IN-PD-EXEMPT" else ".")),
            fix="Ask the insurer for the computation sheet and claim the difference citing the rule below.",
            irdai_cite=_rcite(b, rule), tool_evidence={"correct_payout": base.payout, "insurer_paid": rej.amount_paid})
    for f in F:
        f.explanations["en"] = f.explanation_en
    return F
