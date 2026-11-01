"""S5 Verify: Python has the last word. Recompute money, validate quotes, drop uncited agent claims."""
from __future__ import annotations
from rapidfuzz import fuzz
from core.schemas import Finding
from core.tools import checks as C
from core.tools.payout import compute_payout


def _policy_text(b: C.Bundle) -> list[tuple[int, str]]:
    return [(c.page, " ".join(c.text.split())) for c in b.policy.clauses]


def verify(b: C.Bundle, findings: list[Finding], *_ignored) -> list[Finding]:
    base = compute_payout(b)
    insurer_paid = b.rejection.amount_paid if (b.rejection and b.ctx.mode == "appeal") else None
    dmap = {d.id: d.amount for d in base.deductions}
    texts = _policy_text(b)
    keep: list[Finding] = []
    for f in findings:
        notes = f.verifier_notes
        ok = True
        # 1. money: tools override model
        if f.deduction_ids:
            amt = round(sum(dmap.get(d, 0) for d in f.deduction_ids), 2)
            if abs(amt - f.inr_impact) > 1 and f.issue_type not in ("ped",):
                notes.append(f"amount corrected {f.inr_impact:.0f} → {amt:.0f} by payout engine")
            f.inr_impact = amt
            if amt == 0 and f.issue_type not in ("waiting_period", "wrongful_rejection", "missing_doc"):
                notes.append("deduction no longer present after recomputation — dropped"); continue
        elif f.issue_type not in ("missing_doc", "copay", "ped", "intimation", "short_settlement") and f.inr_impact and f.raised_by in ("tpa", "advocate", "critic"):
            notes.append("model-stated amount without engine deduction → set to 0"); f.inr_impact = 0
        if f.issue_type == "copay":
            f.inr_impact = base.copay
        if f.issue_type == "short_settlement":
            f.inr_impact = round(max(0.0, base.payout - (insurer_paid or 0)), 2) if insurer_paid is not None else 0
            if f.inr_impact <= 0.5:
                notes.append("no short-settlement gap after recomputation — dropped"); continue
        # 2. policy quote must exist in the policy text
        if f.policy_cite and f.policy_cite.quote:
            q = " ".join(f.policy_cite.quote.split())
            best = max(((fuzz.partial_ratio(q[:300], t), p) for p, t in texts), default=(0, None))
            if best[0] >= 85:
                f.policy_cite.verified = True
                f.policy_cite.page = f.policy_cite.page or best[1]
            elif texts:
                notes.append(f"policy quote not found in wording (score {best[0]:.0f}) — citation removed")
                f.policy_cite = None; ok = False
        # 3. regulator citation provenance
        if f.irdai_cite and not f.irdai_cite.verified:
            notes.append("regulator rule text is a seed paraphrase — verify against circular")
        # 4. agent-only findings need some citation
        if f.raised_by == "tpa" and not f.deduction_ids and not (f.policy_cite or f.irdai_cite) and f.issue_type != "ped":
            notes.append("uncited agent risk — dropped"); continue
        # 5. confidence gate
        if f.confidence < 0.6 and f.status in ("contestable", "fixable"):
            notes.append("low confidence → needs review"); f.status = "needs_review"
        f.verified = ok and (f.policy_cite is None or f.policy_cite.verified) and bool(f.deduction_ids or f.issue_type in ("missing_doc", "copay", "intimation", "ped", "short_settlement", "exclusion"))
        keep.append(f)
    for n, f in enumerate(keep, 1):
        f.id = f"F{n:02d}"
    return keep
