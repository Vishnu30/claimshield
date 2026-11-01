"""S7 Report: payouts under three scenarios (as-is / after fixes / after appeal), risk score, action plan."""
from __future__ import annotations
from datetime import datetime
from core.schemas import ClaimReport, Finding
from core.tools import checks as C
from core.tools.payout import compute_payout
from core.fmt import money

WHO = {"mismatch": "Hospital billing desk", "non_payable": "Hospital billing desk", "proportionate": "Hospital (certificate) / Insurer",
       "room_rent": "You", "waiting_period": "You → Insurer", "missing_doc": "Hospital / You", "wrongful_rejection": "Insurer GRO → Ombudsman",
       "intimation": "Insurer", "ped": "You / Doctor", "sublimit": "—", "copay": "—", "exclusion": "Doctor / Insurer", "icu_rent": "You",
       "overbilling": "Hospital", "other": "You", "short_settlement": "Insurer"}


def build(b: C.Bundle, findings: list[Finding], trace: list[dict], cost: float, calls: int, engine: str) -> ClaimReport:
    pack = b.pack
    cur, style = pack.get("currency", ""), pack.get("number_format", "western")
    M = lambda x: money(x, cur, style)
    appeal = b.ctx.mode == "appeal"
    fix_ids = {d for f in findings if f.status == "fixable" for d in f.deduction_ids}
    con_ids = {d for f in findings if f.status == "contestable" for d in f.deduction_ids}
    before = compute_payout(b)
    after_fix = compute_payout(b, waive=fix_ids)
    after_app = compute_payout(b, waive=fix_ids | con_ids)
    insurer_paid = b.rejection.amount_paid if (appeal and b.rejection and b.rejection.amount_paid is not None) else None
    p_before = insurer_paid if insurer_paid is not None else before.payout
    if appeal:
        p_after_fix = max(p_before, after_fix.payout)
        recover = max(0.0, after_app.payout - p_before)
        pp_before = before.claimed - p_before
        pp_after = after_app.patient_pays
    else:
        p_after_fix = after_fix.payout
        recover = max(0.0, after_app.payout - after_fix.payout)
        pp_before, pp_after = before.patient_pays, after_fix.patient_pays
    docs = C.required_docs(b)["missing"]
    claimed = before.claimed or 1
    at_risk = claimed - p_before
    crit = sum(1 for d in docs if d["critical"])
    nr = sum(1 for f in findings if f.status == "needs_review")
    risk = min(100, int(round(at_risk / claimed * 100 * 1.1 + 8 * crit + 5 * nr)))
    band = "low" if risk < 25 else "medium" if risk < 55 else "high"

    n_fix = sum(1 for f in findings if f.status == "fixable")
    if appeal:
        summary = (f"Claimed {M(before.claimed)}; insurer paid {M(p_before)}. Our audit finds {M(recover)} recoverable through appeal "
                   f"on {sum(1 for f in findings if f.status == 'contestable')} ground(s).")
    elif not findings:
        summary = f"No issues found. Expected payout {M(p_before)} of {M(before.claimed)} claimed."
    else:
        summary = (f"Claimed {M(before.claimed)}. As it stands the insurer is likely to pay {M(p_before)} (cut {M(at_risk)}). "
                   f"Fixing {n_fix} issue(s) before {'discharge' if b.ctx.mode == 'pre_discharge' else 'submission'} raises this to {M(p_after_fix)}"
                   + (f"; a further {M(recover)} can be contested." if recover > 0 else "."))
    plan = []
    order = sorted([f for f in findings if f.status in ("fixable", "contestable", "needs_review")],
                   key=lambda f: ({"fixable": 0, "contestable": 1, "needs_review": 2}[f.status], -f.inr_impact))
    for n, f in enumerate(order, 1):
        plan.append({"step": n, "finding": f.id, "who": WHO.get(f.issue_type, "You"), "action": f.fix or f.title, "status": f.status,
                     "impact": f.inr_impact, "when": "before discharge" if b.ctx.mode == "pre_discharge" and f.status == "fixable"
                     else "before submission" if f.status == "fixable" else "in appeal" if f.status == "contestable" else "check"})
    return ClaimReport(mode=b.ctx.mode, jurisdiction=b.ctx.jurisdiction, user_lang=b.ctx.user_lang, currency=cur,
                       claimed=before.claimed, payout_before=p_before, payout_after_fixes=p_after_fix, payout_after_appeal=max(after_app.payout, p_after_fix),
                       recoverable_via_appeal=recover, patient_pays_before=pp_before, patient_pays_after=pp_after, insurer_paid=insurer_paid,
                       risk_score=risk, risk_band=band, summary_en=summary, summary_localized=summary, findings=findings, missing_docs=docs,
                       action_plan=plan, trace=trace, cost_usd=round(cost, 4), llm_calls=calls, engine=engine,
                       generated_at=datetime.now().isoformat(timespec="seconds"), payout_detail_before=before)
