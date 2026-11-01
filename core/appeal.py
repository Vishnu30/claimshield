"""S8 Appeal / grievance letters + hospital fix-request letter -> JSON (Muse) -> PDF (PyMuPDF HTML engine, all scripts).
PII never leaves the machine: Muse writes with placeholders; we fill them locally."""
from __future__ import annotations
import io, json
from datetime import date
from core import llm, prompts, kb
from core.schemas import ClaimReport
from core.tools import checks as C
from core.fmt import money

PLACEHOLDERS = ["[PATIENT_NAME]", "[POLICY_NO]", "[CLAIM_NO]", "[HOSPITAL]", "[ADMISSION_DATE]", "[INSURER]", "[CONTACT]"]


def _m(b, x):
    return money(x, b.pack.get("currency", ""), b.pack.get("number_format", "western"))


def _template(b: C.Bundle, rep: ClaimReport, kind: str) -> dict:
    items = [f for f in rep.findings if (f.status == "contestable" if kind == "appeal" else f.status == "fixable")]
    if kind == "hospital":
        return {"subject": "Request to correct the final bill before insurance submission — [PATIENT_NAME]",
                "salutation": "Dear Billing / TPA Desk,",
                "paragraphs": ["I am writing regarding the in-patient bill for [PATIENT_NAME] (admitted [ADMISSION_DATE]). Before we submit the claim to [INSURER], an audit against the policy shows items the insurer is likely to deduct that the hospital can correct."],
                "grounds": [{"title": f.title, "text": f"{f.fix or ''} (Impact: {_m(b, f.inr_impact)})", "cites": []} for f in items],
                "relief": "Kindly issue the corrected/itemized documents so the claim is not short-paid.",
                "enclosures": ["Copy of current bill"], "closing": "Thank you,\n[PATIENT_NAME]\n[CONTACT]"}
    reasons = "; ".join(r.text for r in (b.rejection.reasons if b.rejection else [])) or "the deductions made"
    grounds = []
    for f in items:
        cites = []
        if f.policy_cite and f.policy_cite.quote:
            cites.append(f"Policy clause (p.{f.policy_cite.page}): “{f.policy_cite.quote[:260]}”")
        if f.irdai_cite:
            cites.append(f"{f.irdai_cite.doc}: {f.irdai_cite.quote[:260]}")
        grounds.append({"title": f.title, "text": (f.tool_evidence.get("advocate_argument") or f.explanation_en), "cites": cites})
    return {"subject": "Appeal against repudiation / short-settlement of claim [CLAIM_NO] under policy [POLICY_NO]",
            "salutation": "To the Grievance Redressal Officer, [INSURER]",
            "paragraphs": [f"I, [PATIENT_NAME], hold policy [POLICY_NO]. My claim [CLAIM_NO] for hospitalisation at [HOSPITAL] from [ADMISSION_DATE] was decided citing: {reasons}.",
                           "I respectfully submit that the decision is contrary to the policy terms and the applicable regulations, on the following grounds."],
            "grounds": grounds,
            "relief": f"I request you to review the decision and settle {_m(b, rep.recoverable_via_appeal)} with applicable interest, within the regulatory timeline.",
            "enclosures": ["Policy copy", "Rejection / settlement letter", "Final itemized bill", "Discharge summary"],
            "closing": "Yours faithfully,\n[PATIENT_NAME]\n[CONTACT]"}


def draft(b: C.Bundle, rep: ClaimReport, kind: str = "appeal") -> dict:
    """kind: appeal | hospital"""
    st = llm.stats()
    base = _template(b, rep, kind)
    if not base["grounds"]:
        return base
    payload = {"kind": kind, "mode": rep.mode, "jurisdiction": b.pack["name"], "currency": b.pack.get("currency"),
               "rejection": b.rejection.model_dump(exclude={"source_file"}) if b.rejection else None,
               "amount_sought": rep.recoverable_via_appeal if kind == "appeal" else rep.payout_after_fixes - rep.payout_before,
               "findings": [{"title": f.title, "status": f.status, "amount": f.inr_impact, "explanation": f.explanation_en, "fix": f.fix,
                             "argument": f.tool_evidence.get("advocate_argument"),
                             "policy_clause": f.policy_cite.model_dump() if f.policy_cite else None,
                             "regulator_rule": f.irdai_cite.model_dump() if f.irdai_cite else None,
                             "precedents": [p for p in kb.precedents(b.ctx.jurisdiction) if p["id"] in f.precedent_ids and not p.get("illustrative")]}
                            for f in rep.findings if f.status in (("contestable",) if kind == "appeal" else ("fixable",))],
               "placeholders": PLACEHOLDERS, "template": base}
    try:
        out, res = llm.complete_json("appeal", prompts.APPEAL.format(doc_kind="appeal letter" if kind == "appeal" else "bill-correction request to the hospital",
                                                                     jurisdiction=b.pack["name"]),
                                     json.dumps(payload, ensure_ascii=False, default=str), {"type": "object"}, label=f"appeal:{kind}", max_tokens=3000)
        st.emit({"stage": "appeal", "kind": "done", "text": f"{kind} letter drafted via {res['provider']}"})
        for k in base:
            out.setdefault(k, base[k])
        return out
    except (llm.LLMUnavailable, ValueError) as e:
        st.emit({"stage": "appeal", "kind": "fallback", "text": f"template letter used ({str(e)[:80]})"})
        return base


def fill(letter: dict, values: dict[str, str]) -> dict:
    s = json.dumps(letter, ensure_ascii=False)
    for k, v in values.items():
        if v:
            s = s.replace(k, v.replace('"', "'"))
    return json.loads(s)


def to_text(letter: dict) -> str:
    out = [letter.get("subject", ""), "", letter.get("salutation", ""), ""]
    out += [p for p in letter.get("paragraphs", [])] + [""]
    for n, g in enumerate(letter.get("grounds", []), 1):
        out.append(f"{n}. {g.get('title','')}: {g.get('text','')}")
        out += [f"   - {c}" for c in g.get("cites", [])]
    out += ["", letter.get("relief", ""), "", "Enclosures: " + ", ".join(letter.get("enclosures", [])), "", letter.get("closing", "")]
    return "\n".join(out)


def to_pdf(letter: dict, rep: ClaimReport, b: C.Bundle, escalation: list[dict] | None = None) -> bytes:
    """Letter + annexures as PDF (HTML layout engine: names, addresses or notes in any script render correctly)."""
    from html import escape as E
    from core.pdfhtml import html_to_pdf
    M = lambda x: E(money(x, b.pack.get("currency", "") or "₹", b.pack.get("number_format", "western")))
    P = lambda t: "".join(f"<p>{E(x).replace(chr(10), '<br/>')}</p>" for x in str(t or "").split("\n\n"))
    h = [f"<p class='muted'>{date.today():%d %B %Y}</p>", P(letter.get("salutation", "")), f"<p><b>Subject: {E(letter.get('subject', ''))}</b></p>"]
    h += [P(p) for p in letter.get("paragraphs", [])]
    for n, g in enumerate(letter.get("grounds", []), 1):
        h.append(f"<h3>Ground {n}: {E(g.get('title', ''))}</h3>{P(g.get('text', ''))}")
        h += [f"<p class='quote'>{E(c)}</p>" for c in g.get("cites", [])]
    h += [P(letter.get("relief", "")), P(letter.get("closing", ""))]
    h.append("<h2>Annexure A — Claim audit summary</h2><table><tr><th>#</th><th>Issue</th><th>Status</th><th>Amount</th></tr>"
             + "".join(f"<tr><td>{i}</td><td>{E(f.title)}</td><td>{E(f.status)}</td><td>{M(f.inr_impact)}</td></tr>" for i, f in enumerate(rep.findings, 1)) + "</table>")
    if escalation:
        h.append("<h2>Annexure B — Escalation path if unresolved</h2><ol>" + "".join(
            f"<li>{E(e['step'])} — {E(e['how'])}" + (f" (by {E(e['deadline'])})" if e.get("deadline") else "") + "</li>" for e in escalation) + "</ol>")
    h.append(f"<p class='muted'>Enclosures: {E(', '.join(letter.get('enclosures', [])))}</p>"
             "<p class='muted'>Prepared with ClaimShield (open-source claim auditor). Amounts computed deterministically from the policy and bill.</p>")
    return html_to_pdf("".join(h))
