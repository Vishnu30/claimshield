"""Muse agent roles: TPA Adjudicator, Patient Advocate, Critic, Localizer, Appeal Writer, Policy Chat, Single-pass baseline.
Each role degrades gracefully to the rules engine when credits/providers are unavailable."""
from __future__ import annotations
import json
from typing import Optional
from core import llm, prompts, kb
from core.tools import registry, checks as C
from core.tools.payout import compute_payout
from core.schemas import Finding, Citation


def _claim_json(b: C.Bundle) -> dict:
    p = b.policy
    return {
        "policy": {"id": p.policy_id, "insurer": p.insurer, "sum_insured": b.si,
                   "room_rent": p.room_rent.model_dump(exclude={"cite"}), "icu_rent": p.icu_rent.model_dump(exclude={"cite"}),
                   "proportionate_deduction": p.proportionate_deduction, "copays": [c.model_dump(exclude={"cite"}) for c in p.copays],
                   "sublimits": [s.model_dump(exclude={"cite"}) for s in p.sublimits],
                   "waiting_periods": [{"kind": w.kind, "months": w.months, "conditions": w.conditions, "clause": w.cite.clause_id} for w in p.waiting_periods],
                   "exclusions": [{"code": e.code, "text": e.text[:160]} for e in p.exclusions][:20],
                   "clause_ids": [c.id for c in p.clauses][:80]},
        "bill": {"admission": b.bill.admission_date, "discharge": b.bill.discharge_date, "room_category": b.bill.room_category,
                 "room_days": b.bill.room_days, "stated_total": b.bill.stated_total, "discount": b.bill.discount, "package": b.bill.package,
                 "differential_billing": b.bill.differential_billing,
                 "lines": [[i.id, i.description, i.category, i.amount] for i in b.bill.line_items]},
        "discharge": b.discharge.model_dump(exclude={"source_file"}),
        "context": b.ctx.model_dump(exclude={"narrative"}),
        "patient_narrative": b.ctx.narrative,
        "rejection": b.rejection.model_dump(exclude={"source_file"}) if b.rejection else None,
    }


# ------------------------------------------------------------------ TPA
def tpa_adjudicate(b: C.Bundle, ev: dict, findings: list[Finding]) -> list[Finding]:
    st = llm.stats()
    st.emit({"stage": "tpa", "kind": "start", "text": "TPA Adjudicator reviewing claim like the insurer would"})
    base = compute_payout(b)
    pack = b.pack
    specs, fns = registry(b)
    user = json.dumps({"claim": _claim_json(b), "evidence_pack": ev,
                       "engine_deductions": [d.model_dump() for d in base.deductions],
                       "rules_engine_findings": [{"id": f.id, "issue_type": f.issue_type, "title": f.title, "inr": f.inr_impact, "deduction_ids": f.deduction_ids} for f in findings]},
                      default=str, ensure_ascii=False)
    try:
        out, steps = llm.run_tool_agent("tpa", prompts.TPA.format(jurisdiction=pack["name"], regulator=pack.get("regulator", "")),
                                        user, specs, fns, {"type": "object"}, label="tpa")
    except llm.LLMUnavailable as e:
        st.emit({"stage": "tpa", "kind": "fallback", "text": f"rules engine used ({str(e)[:120]})"})
        for f in findings:
            f.raised_by = "rules"
        return findings
    # re-detect if the agent recategorised items (bill changed)
    if any(s["tool"] == "recategorize_item" for s in steps):
        from core.detectors import detect, evidence_pack
        findings = detect(b, evidence_pack(b))
        st.emit({"stage": "tpa", "kind": "info", "text": "bill re-categorised by TPA → findings recomputed by tools"})
    by_ded = {d: f for f in findings for d in f.deduction_ids}
    for d in (out or {}).get("deductions", []):
        f = by_ded.get(d.get("deduction_id"))
        if not f:
            continue
        f.raised_by = "tpa"
        if d.get("agree") is False:
            f.verifier_notes.append(f"TPA disagrees: {d.get('reasoning','')[:200]}")
            f.confidence = min(f.confidence, 0.55)
        else:
            f.confidence = max(f.confidence, float(d.get("confidence") or f.confidence))
        if d.get("reasoning"):
            f.tool_evidence["tpa_reasoning"] = d["reasoning"][:600]
        if d.get("clause_id") and not f.policy_cite:
            c = (b.clause_index or kb.ClauseIndex(b.policy.clauses)).get(d["clause_id"])
            if c:
                f.policy_cite = Citation(doc="policy", page=c.page, quote=c.text[:400])
    for r in (out or {}).get("extra_risks", [])[:5]:
        it = r.get("issue_type") if r.get("issue_type") in ("ped", "exclusion", "overbilling", "intimation", "other") else "other"
        if any(f.issue_type == it for f in findings):
            continue
        f = Finding(id=f"F{len(findings) + 1:02d}", issue_type=it, title=str(r.get("title", "Risk"))[:120], raised_by="tpa",
                    status="needs_review", inr_impact=0, confidence=float(r.get("confidence") or 0.5),
                    explanation_en=str(r.get("reasoning", ""))[:800])
        if r.get("clause_id"):
            c = (b.clause_index or kb.ClauseIndex(b.policy.clauses)).get(r["clause_id"])
            if c:
                f.policy_cite = Citation(doc="policy", page=c.page, quote=c.text[:400])
        f.explanations["en"] = f.explanation_en
        findings.append(f)
    st.emit({"stage": "tpa", "kind": "done", "text": f"{len(findings)} predicted issues; {len(steps)} tool calls", "notes": (out or {}).get("notes", "")})
    return findings


# ------------------------------------------------------------------ Advocate
def advocate(b: C.Bundle, findings: list[Finding]) -> list[Finding]:
    st = llm.stats()
    st.emit({"stage": "advocate", "kind": "start", "text": "Patient Advocate challenging each predicted cut"})
    if not findings:
        return findings
    specs, fns = registry(b, {"search_rules", "search_precedents", "get_clause", "ped_check", "waiting_period_check"})
    user = json.dumps({"mode": b.ctx.mode, "narrative": b.ctx.narrative, "context": b.ctx.model_dump(exclude={"narrative"}),
                       "findings": [{"id": f.id, "issue_type": f.issue_type, "title": f.title, "amount": f.inr_impact, "default_status": f.status,
                                     "explanation": f.explanation_en, "evidence": f.tool_evidence,
                                     "regulator_rule": f.irdai_cite.doc if f.irdai_cite else None} for f in findings]},
                      default=str, ensure_ascii=False)
    try:
        out, steps = llm.run_tool_agent("advocate", prompts.ADVOCATE.format(jurisdiction=b.pack["name"], mode=b.ctx.mode),
                                        user, specs, fns, {"type": "object"}, label="advocate")
    except llm.LLMUnavailable as e:
        st.emit({"stage": "advocate", "kind": "fallback", "text": f"rule-based statuses kept ({str(e)[:120]})"})
        return findings
    fm = {f.id: f for f in findings}
    for v in (out or {}).get("verdicts", []):
        f = fm.get(v.get("id"))
        if not f:
            continue
        s = v.get("status")
        if s in ("valid", "fixable", "contestable", "needs_review") and s != f.status:
            # money-bearing status changes must be backed by a rule/precedent id or tool evidence
            if s == "contestable" and not (v.get("rule_ids") or v.get("precedent_ids")):
                f.verifier_notes.append("advocate proposed 'contestable' without a rule id — kept rules status")
            else:
                f.verifier_notes.append(f"advocate: {f.status} → {s}")
                f.status = s
        if v.get("fix"):
            f.fix = str(v["fix"])[:600]
        if v.get("argument"):
            f.tool_evidence["advocate_argument"] = str(v["argument"])[:800]
        for rid in v.get("rule_ids") or []:
            r = kb.rule(b.ctx.jurisdiction, rid)
            if r and not f.irdai_cite:
                f.irdai_cite = Citation(doc=f"{r['source']} [{r['id']}]", page=r.get("page"), quote=r.get("quote") or r["rule"], verified=bool(r.get("quote") and not r.get("seed")))
        f.precedent_ids = [p for p in (v.get("precedent_ids") or []) if any(x["id"] == p for x in kb.precedents(b.ctx.jurisdiction))]
        f.raised_by = "advocate" if f.raised_by == "rules" else f.raised_by
    st.emit({"stage": "advocate", "kind": "done", "text": f"verdicts for {len((out or {}).get('verdicts', []))} findings; {len(steps)} tool calls"})
    return findings


# ------------------------------------------------------------------ Critic
CRITIC_SCHEMA = {"type": "object", "properties": {"reviews": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "keep": {"type": "boolean"}, "status_override": {"type": ["string", "null"]}, "concern": {"type": ["string", "null"]},
    "confidence": {"type": ["number", "null"]}}}}, "overall": {"type": "string"}}}


def critic_review(b: C.Bundle, findings: list[Finding]) -> dict | None:
    """The Critic's call only (no mutation) — so it can run in parallel with the jury."""
    st = llm.stats()
    if not findings:
        return None
    st.emit({"stage": "critic", "kind": "start", "text": "Critic auditing statuses, fixes and citations"})
    payload = [{"id": f.id, "issue": f.issue_type, "status": f.status, "amount": round(f.inr_impact), "explanation": (f.explanation_en or "")[:300],
                "fix": (f.fix or "")[:200], "policy_quote": f.policy_cite.quote[:200] if f.policy_cite and f.policy_cite.quote else None,
                "rule": f.irdai_cite.quote[:200] if f.irdai_cite else None} for f in findings]
    try:
        out, _ = llm.complete_json("critic", prompts.CRITIC, json.dumps(payload, ensure_ascii=False), CRITIC_SCHEMA, label="critic",
                                   reasoning=False, max_tokens=1200)
        return out if isinstance(out, dict) else None
    except (llm.LLMUnavailable, ValueError) as e:
        st.emit({"stage": "critic", "kind": "fallback", "text": f"skipped ({str(e)[:100]})"})
        return None


def apply_critic(findings: list[Finding], out: dict | None, protect: set[str] | None = None) -> list[Finding]:
    """Apply the Critic's review; statuses the jury already decided (protect) are not overridden."""
    if not out:
        return findings
    protect = protect or set()
    fm = {f.id: f for f in findings}
    for r in out.get("reviews", []) or []:
        f = fm.get(r.get("id")) if isinstance(r, dict) else None
        if not f:
            continue
        if r.get("concern"):
            f.verifier_notes.append(f"critic: {str(r['concern'])[:200]}")
        if r.get("keep") is False and f.raised_by == "tpa" and f.inr_impact == 0:
            f.status = "needs_review"
        so = r.get("status_override")
        if so in ("valid", "fixable", "contestable", "needs_review") and so != f.status and f.issue_type not in ("copay", "sublimit") and f.id not in protect:
            f.verifier_notes.append(f"critic override {f.status} → {so}")
            f.status = so
        if isinstance(r.get("confidence"), (int, float)):
            f.confidence = round((f.confidence + float(r["confidence"])) / 2, 2)
    llm.stats().emit({"stage": "critic", "kind": "done", "text": str(out.get("overall") or "review complete")[:300]})
    return findings


def critic(b: C.Bundle, findings: list[Finding]) -> list[Finding]:
    return apply_critic(findings, critic_review(b, findings))


def jury_and_critic(b: C.Bundle, findings: list[Finding]) -> list[Finding]:
    """Jury (3 hosts) and Critic run at the same time — one round of latency instead of two."""
    import contextvars, copy
    from concurrent.futures import ThreadPoolExecutor
    snapshot = copy.deepcopy(findings)
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(contextvars.copy_context().run, critic_review, b, snapshot)
        before = {f.id: f.status for f in findings}
        findings = jury(b, findings)
        try:
            review = fut.result(timeout=90)
        except Exception:  # noqa: BLE001
            review = None
    llm.stats().flush()
    changed = {f.id for f in findings if before.get(f.id) != f.status}
    return apply_critic(findings, review, protect=changed)


# ------------------------------------------------------------------ Localizer
def localize_findings(findings: list[Finding], lang: str) -> int:
    """Translate titles, explanations and fixes in place (keyed by object, so it can run while the jury votes)."""
    from core.translate import translate
    if lang == "en" or not findings:
        return 0
    items, owner = {}, {}
    for n, f in enumerate(findings):
        for field, text in (("title", f.title), ("exp", f.explanation_en), ("fix", f.fix)):
            if text:
                items[f"{n}.{field}"] = text; owner[f"{n}.{field}"] = (f, field)
    tr = translate(items, lang, "loc")
    done = 0
    for k, (f, field) in owner.items():
        v = tr.get(k)
        if not v or v == items[k]:
            continue
        done += 1
        if field == "exp":
            f.explanations[lang] = v
        elif field == "fix":
            f.fix_localized[lang] = v
        else:
            f.tool_evidence.setdefault("title_localized", {})[lang] = v
    return done


def localize(findings: list[Finding], summary: str, lang: str) -> tuple[list[Finding], str]:
    """Findings, fixes and the summary → patient's language (parallel small batches; ₹ and clause ids preserved)."""
    from core.translate import translate
    if lang == "en":
        return findings, summary
    st = llm.stats()
    items = {"summary": summary}
    for f in findings:
        items[f"{f.id}.title"], items[f"{f.id}.exp"] = f.title, f.explanation_en
        if f.fix:
            items[f"{f.id}.fix"] = f.fix
    tr = translate(items, lang, "loc")
    done = sum(1 for k in items if tr.get(k) != items[k])
    for f in findings:
        if tr.get(f"{f.id}.exp") and tr[f"{f.id}.exp"] != f.explanation_en:
            f.explanations[lang] = tr[f"{f.id}.exp"]
        if f.fix and tr.get(f"{f.id}.fix") and tr[f"{f.id}.fix"] != f.fix:
            f.fix_localized[lang] = tr[f"{f.id}.fix"]
        if tr.get(f"{f.id}.title") and tr[f"{f.id}.title"] != f.title:
            f.tool_evidence.setdefault("title_localized", {})[lang] = tr[f"{f.id}.title"]
    st.emit({"stage": "localize", "kind": "done" if done else "fallback",
             "text": f"{done}/{len(items)} texts translated to {prompts.LANG_NAMES.get(lang, lang)}" if done else "translation unavailable — English shown"})
    return findings, tr.get("summary") or summary


# ------------------------------------------------------------------ Policy chat
def ask_policy(b: C.Bundle, question: str, lang: str = "en") -> dict:
    idx = b.clause_index or kb.ClauseIndex(b.policy.clauses)
    clauses = idx.search(question, 4)
    rules = kb.search_rules(b.ctx.jurisdiction, question, 3)
    src = {"clauses": [{"id": c.id, "page": c.page, "text": c.text[:900]} for c in clauses],
           "rules": [{"id": r["id"], "text": r["rule"], "source": r["source"]} for r in rules]}
    try:
        res = llm.complete("chat", [{"role": "system", "content": prompts.CHAT.format(lang_name=prompts.LANG_NAMES.get(lang, lang))},
                                    {"role": "user", "content": json.dumps({"question": question, "sources": src}, ensure_ascii=False)}],
                           max_tokens=600, label="chat")
        return {"answer": res["content"], "sources": src, "provider": res["provider"], "cached": res.get("cached")}
    except llm.LLMUnavailable:
        top = clauses[0] if clauses else None
        return {"answer": (f"Most relevant clause [{top.id}] (p.{top.page}): {top.text[:500]}" if top else "No matching clause found."),
                "sources": src, "provider": "offline", "cached": False}


# ------------------------------------------------------------------ Single-pass baseline (ablation)
def single_pass(b: C.Bundle) -> list[Finding]:
    out, _ = llm.complete_json("single", prompts.SINGLE, json.dumps(_claim_json(b), default=str, ensure_ascii=False), {"type": "object"}, label="single")
    F = []
    for n, i in enumerate((out or {}).get("issues", []), 1):
        it = i.get("issue_type") or "other"
        try:
            F.append(Finding(id=f"F{n:02d}", issue_type=it, title=str(i.get("title", ""))[:120], raised_by="tpa",
                             status=i.get("status") if i.get("status") in ("valid", "fixable", "contestable", "needs_review") else "needs_review",
                             inr_impact=float(i.get("amount") or 0), confidence=0.5, explanation_en=str(i.get("title", ""))))
        except Exception:
            continue
    return F


# ------------------------------------------------------------------ Cross-provider jury
JURY_SCHEMA = {"type": "object", "properties": {"votes": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "status": {"type": "string", "enum": ["valid", "fixable", "contestable", "needs_review"]}}, "required": ["id", "status"]}}},
    "required": ["votes"]}


def jury(b: C.Bundle, findings: list[Finding], timeout_s: float = 60) -> list[Finding]:
    """Muse API + Muse-open on Baseten + Muse-open on Fireworks vote IN PARALLEL on money-bearing judgements.
    Compact prompt, tiny answer ({id,status} only), one call per host, hard timeout. Majority (≥2) wins; agreement → confidence."""
    import config
    import contextvars
    from concurrent.futures import ThreadPoolExecutor, wait
    from collections import Counter
    st = llm.stats()
    panel = [p for p in config.JURY_PROVIDERS if llm.provider_ready(p) or config.LLM_MODE == "replay"]
    cands = [f for f in findings if f.status in ("contestable", "fixable", "needs_review") and f.issue_type not in ("missing_doc", "copay")][:6]
    if len(panel) < 2 or not cands:
        st.emit({"stage": "jury", "kind": "info", "text": f"skipped ({len(panel)} provider(s) ready, {len(cands)} candidate(s))"})
        return findings
    st.emit({"stage": "jury", "kind": "start", "text": f"{len(panel)}-host jury on {len(cands)} judgement(s), in parallel: {', '.join(panel)}"})
    payload = json.dumps({"mode": b.ctx.mode, "findings": [{"id": f.id, "issue": f.issue_type, "amount": round(f.inr_impact), "proposed": f.status,
                                                            "why": (f.explanation_en or "")[:260],
                                                            "rule": (f.irdai_cite.quote[:200] if f.irdai_cite else None),
                                                            "clause": (f.policy_cite.quote[:200] if f.policy_cite else None)} for f in cands]},
                         ensure_ascii=False)

    def vote(p):
        out, _ = llm.complete_json("jury", prompts.JURY.format(jurisdiction=b.pack["name"]), payload, JURY_SCHEMA,
                                   label=f"jury:{p}", providers=[p], reasoning=False, max_tokens=600)
        return {v.get("id"): v.get("status") for v in (out or {}).get("votes", []) if isinstance(v, dict)
                and v.get("status") in ("valid", "fixable", "contestable", "needs_review")}

    ballots: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=len(panel)) as ex:
        futs = {ex.submit(contextvars.copy_context().run, vote, p): p for p in panel}
        done, pending = wait(futs, timeout=timeout_s)
        for fu in done:
            p = futs[fu]
            try:
                ballots[p] = fu.result()
            except Exception as e:  # noqa: BLE001
                st.emit({"stage": "jury", "kind": "fallback", "text": f"{p} abstained ({str(e)[:80]})"})
        for fu in pending:
            st.emit({"stage": "jury", "kind": "fallback", "text": f"{futs[fu]} abstained (no answer within {timeout_s:.0f}s)"})
            fu.cancel()
    st.flush()
    ballots = {p: v for p, v in ballots.items() if v}
    if len(ballots) < 2:
        st.emit({"stage": "jury", "kind": "info", "text": f"fewer than 2 votes ({', '.join(ballots) or 'none'}) — statuses unchanged"})
        return findings
    for f in cands:
        votes = {p: bal.get(f.id) for p, bal in ballots.items() if bal.get(f.id)}
        f.tool_evidence["jury"] = votes
        if not votes:
            continue
        top, n = Counter(votes.values()).most_common(1)[0]
        f.confidence = round(0.5 * f.confidence + 0.5 * n / len(votes), 2)
        if n >= 2 and top != f.status:
            if top == "contestable" and not f.irdai_cite:
                f.verifier_notes.append(f"jury majority '{top}' ignored — no regulator rule cited")
            else:
                f.verifier_notes.append(f"jury {n}/{len(votes)}: {f.status} → {top}")
                f.status = top
    st.emit({"stage": "jury", "kind": "done", "text": f"votes from {', '.join(ballots)}; "
             f"unanimous on {sum(1 for f in cands if len(set(f.tool_evidence.get('jury', {}).values())) == 1)}/{len(cands)}"})
    return findings
