"""Deterministic checks. Muse decides *what* to check and *how to judge*; these functions own every number."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from rapidfuzz import fuzz
from core.schemas import PolicyRules, Bill, Discharge, Rejection, ClaimContext, LineItem
from core import kb

ASSOCIATED = {"room", "nursing", "consultation", "surgeon", "anaesthesia", "ot", "procedure", "misc"}
PD_EXEMPT = {"pharmacy", "consumable", "implant", "investigation", "unitemized"}


@dataclass
class Bundle:
    policy: PolicyRules
    bill: Bill
    ctx: ClaimContext
    discharge: Discharge = field(default_factory=Discharge)
    rejection: Optional[Rejection] = None
    clause_index: Optional[kb.ClauseIndex] = None

    @property
    def si(self) -> Optional[float]:
        return self.ctx.sum_insured or self.policy.sum_insured

    @property
    def pack(self) -> dict:
        return kb.jurisdiction(self.ctx.jurisdiction)

    @property
    def feats(self) -> dict:
        return self.pack.get("features", {})

    def clinical_text(self) -> str:
        d = self.discharge
        return " ".join(filter(None, [d.diagnosis, d.procedure, d.icd10, d.history_notes, " ".join(d.comorbidities)])).lower()


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


def months_between(a: date, b: date) -> int:
    m = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        m -= 1
    return max(m, 0)


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9& ]+", " ", (s or "").lower()).split())


def line_sum(bill: Bill) -> float:
    return round(sum(i.amount for i in bill.line_items), 2)


def claimed_amount(bill: Bill) -> float:
    return round(bill.stated_total if bill.stated_total is not None else line_sum(bill) - bill.discount, 2)


# ---------------------------------------------------------------- room rent / PD
def _limit_per_day(limit, si: Optional[float]) -> Optional[float]:
    if limit.kind == "pct_si" and limit.value is not None and si:
        v = si * limit.value / 100.0
        return min(v, limit.max_value) if limit.max_value else v
    if limit.kind == "fixed":
        return limit.value
    return None


ROOM_RANK = [("general", 0), ("ward", 0), ("multi", 0), ("shared", 1), ("twin", 1), ("semi", 1), ("single", 2), ("private", 2),
             ("deluxe", 3), ("super", 4), ("suite", 4)]


def room_rank(cat: Optional[str]) -> Optional[int]:
    c = (cat or "").lower()
    ranks = [r for k, r in ROOM_RANK if k in c]
    return max(ranks) if ranks else None


def room_rent_check(b: Bundle, kind: str = "room") -> dict:
    lim = b.policy.room_rent if kind == "room" else b.policy.icu_rent
    items = [i for i in b.bill.line_items if i.category == kind]
    charged = sum(i.amount for i in items)
    days = (b.bill.room_days if kind == "room" else b.bill.icu_days) or sum(i.qty for i in items) or (1 if items else 0)
    per_day = charged / days if days else 0
    cap = _limit_per_day(lim, b.si)
    out = {"kind": kind, "limit_kind": lim.kind, "cap_per_day": cap, "charged_per_day": round(per_day, 2), "days": days,
           "charged_total": charged, "item_ids": [i.id for i in items], "cite": lim.cite.model_dump(),
           "category_limit": lim.category}
    if not items or not b.feats.get("room_rent", True):
        out.update(applicable=False, excess=0, ratio=1.0); return out
    if cap is None:
        allowed, billed = room_rank(lim.category), room_rank(b.bill.room_category)
        above = lim.kind == "category" and allowed is not None and billed is not None and billed > allowed
        out.update(applicable=False, excess=0, ratio=1.0, category_above_entitlement=above,
                   needs_review=lim.kind == "category" and (allowed is None or billed is None)); return out
    excess = max(0.0, charged - cap * days)
    ratio = min(1.0, cap / per_day) if per_day else 1.0
    out.update(applicable=excess > 0.5, excess=round(excess, 2), ratio=ratio)
    return out


def proportionate_check(b: Bundle, working: Optional[dict[str, float]] = None) -> dict:
    rr = room_rent_check(b, "room")
    applies_policy = b.policy.proportionate_deduction and b.feats.get("proportionate_deduction", True)
    w = working or {i.id: i.amount for i in b.bill.line_items}
    assoc = [i for i in b.bill.line_items if i.category in ASSOCIATED - {"room"}]
    exempt = [i for i in b.bill.line_items if i.category in PD_EXEMPT]
    assoc_total = sum(w.get(i.id, 0) for i in assoc)
    pd = assoc_total * (1 - rr["ratio"]) if (applies_policy and rr.get("applicable") and not b.bill.package) else 0.0
    return {"applicable": pd > 0.5, "ratio": rr["ratio"], "associated_total": round(assoc_total, 2),
            "associated_item_ids": [i.id for i in assoc], "exempt_total": round(sum(i.amount for i in exempt), 2),
            "exempt_categories": sorted(PD_EXEMPT - {"unitemized"}), "pd_amount": round(pd, 2),
            "differential_billing": b.bill.differential_billing, "package": b.bill.package,
            "policy_says_pd": b.policy.proportionate_deduction, "cite": b.policy.proportionate_cite.model_dump(),
            "regulator_rules": ["IN-PD-EXEMPT", "IN-PD-NODIFF"] if b.ctx.jurisdiction == "IN" else []}


# ---------------------------------------------------------------- waiting periods / PED
def _cond_match(conds: list[str], text: str) -> Optional[str]:
    for c in conds:
        cn = norm(c)
        t = text.strip()
        if cn and len(t) >= len(cn) and (cn in t or fuzz.partial_ratio(cn, t) >= 92):
            return c
    return None


def waiting_period_check(b: Bundle, condition: Optional[str] = None) -> dict:
    adm = parse_date(b.bill.admission_date)
    start = parse_date(b.ctx.policy_start)
    cont = parse_date(b.ctx.continuous_coverage_since) or start
    if not adm or not start:
        return {"error": "admission_date or policy_start missing", "needs_review": True, "hits": []}
    m_ins = months_between(start, adm)          # what the insurer computes without continuity proof
    m_true = months_between(cont, adm)          # with portability / continuous renewals credited
    text = norm(condition or "") + " " + norm(b.clinical_text())
    feats = b.feats
    hits = []
    for wp in b.policy.waiting_periods:
        req = wp.months
        if wp.kind == "initial":
            if "accident" in text or (b.discharge.admission_type == "emergency" and "injur" in text):
                continue
            matched = "any illness"
        elif wp.kind == "specific":
            matched = _cond_match(wp.conditions, text)
            if not matched:
                continue
        else:  # ped handled below
            continue
        reg_max = feats.get("specific_max_months") if wp.kind == "specific" else None
        eff_req = min(req, reg_max) if reg_max else req
        hits.append({"kind": wp.kind, "condition": matched, "months_required": req, "regulator_max": reg_max,
                     "months_elapsed_insurer_view": m_ins, "months_elapsed_continuous": m_true,
                     "applies_insurer_view": m_ins < req, "applies_correctly": m_true < eff_req,
                     "cite": wp.cite.model_dump()})
    return {"admission": str(adm), "policy_start": str(start), "continuous_since": str(cont),
            "months_insurer_view": m_ins, "months_continuous": m_true, "hits": hits}


CHRONIC = ["diabetes", "hypertension", "thyroid", "asthma", "copd", "coronary", "cardiac", "kidney", "ckd", "arthritis", "epilepsy", "cancer", "hepatitis", "hiv", "dyslipid", "obesity"]


def ped_check(b: Bundle) -> dict:
    adm = parse_date(b.bill.admission_date)
    start = parse_date(b.ctx.policy_start)
    cont = parse_date(b.ctx.continuous_coverage_since) or start
    text = b.clinical_text()
    found = sorted({c for c in CHRONIC if c in text})
    declared = {norm(x) for x in b.ctx.ped_declared}
    undeclared = [c for c in found if not any(c in d or d in c for d in declared)]
    ped_wp = next((w for w in b.policy.waiting_periods if w.kind == "ped"), None)
    m_true = months_between(cont, adm) if (adm and cont) else None
    mor = b.feats.get("moratorium_months")
    ped_max = b.feats.get("ped_max_months")
    req = ped_wp.months if ped_wp else None
    eff_req = min(req, ped_max) if (req and ped_max) else req
    pre_policy = None
    if b.discharge.condition_duration_months is not None and m_true is not None:
        pre_policy = b.discharge.condition_duration_months > m_true
    return {"conditions_in_records": found, "undeclared": undeclared, "condition_predates_policy": pre_policy,
            "months_continuous": m_true, "moratorium_months": mor, "moratorium_passed": bool(mor and m_true is not None and m_true >= mor),
            "ped_waiting_months": req, "regulator_ped_max": ped_max,
            "ped_waiting_served": bool(eff_req is not None and m_true is not None and m_true >= eff_req),
            "cite": ped_wp.cite.model_dump() if ped_wp else None}


# ---------------------------------------------------------------- non-payables
def _np_index(code: str) -> list[tuple[str, dict]]:
    from core import learned
    extra = learned.np_aliases()
    idx = []
    for n in kb.non_payables(code):
        for term in [n["item"], *n.get("aliases", []), *extra.get(n["item"], [])]:
            idx.append((norm(term), n))
    idx.sort(key=lambda x: -len(x[0]))
    return idx


def non_payable_check(b: Bundle) -> dict:
    if not b.feats.get("non_payables", False):
        return {"matches": [], "total": 0, "note": "jurisdiction has no standard non-payable list"}
    idx = _np_index(b.ctx.jurisdiction)
    matches = []
    for it in b.bill.line_items:
        if it.category in ("implant", "pharmacy", "investigation", "surgeon", "anaesthesia", "consultation"):
            continue
        d = norm(it.description)
        hit = None
        if re.search(r"\bkit\b", d) and not re.search(r"\(|:|itemi[sz]ed|breakup", it.description.lower()):
            hit = next((n for t, n in idx if "kit" in t and t != "any kit with no details mentioned"
                        and re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", d)), None)
            if not hit:
                hit = next((n for t, n in idx if t == "any kit with no details mentioned"), None)
        if not hit:
            for t, n in idx:
                if len(t) >= 4 and re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", d):
                    hit = n; break
        if hit:
            matches.append({"item_id": it.id, "description": it.description, "amount": it.amount, "list": hit["list"],
                            "matched": hit["item"], "treatment": hit["treatment"],
                            "unitemized_kit": hit["item"] == "any kit with no details mentioned", "page": it.page})
    return {"matches": matches, "total": round(sum(m["amount"] for m in matches), 2)}


# ---------------------------------------------------------------- sublimit / copay
def sublimit_check(b: Bundle, procedure: Optional[str] = None) -> dict:
    text = norm(procedure or "") + " " + norm(b.clinical_text())
    for s in b.policy.sublimits:
        m = _cond_match([s.procedure, *s.aliases], text)
        if m:
            lims = [x for x in [s.limit, (b.si or 0) * s.pct_si / 100 if s.pct_si else None] if x]
            return {"matched": True, "procedure": s.procedure, "matched_on": m, "limit": min(lims) if lims else None,
                    "basis": s.basis, "cite": s.cite.model_dump()}
    return {"matched": False}


def copay_check(b: Bundle) -> dict:
    if not b.feats.get("copay", True):
        return {"pct": 0, "applied": []}
    conditional = re.compile(r"zone|city|non-?network|network hospital|voluntary|opt(?:ed|ional)|specific disease|if the insured (?:chooses|opts)|room category", re.I)
    applied = [c for c in b.policy.copays
               if (c.age_min is not None and (b.ctx.patient_age or 0) >= c.age_min)
               or (c.age_min is None and not conditional.search(c.condition or ""))]
    pct = max((c.pct for c in applied), default=0)
    best = next((c for c in applied if c.pct == pct), None)
    return {"pct": pct, "condition": best.condition if best else None, "cite": best.cite.model_dump() if best else None,
            "patient_age": b.ctx.patient_age}


# ---------------------------------------------------------------- billing integrity
def reconcile_totals(b: Bundle) -> dict:
    ls = line_sum(b.bill)
    stated = b.bill.stated_total
    net = ls - b.bill.discount
    diff = round((stated - net), 2) if stated is not None else 0.0
    arith = []
    for i in b.bill.line_items:
        if i.rate and i.qty and abs(i.qty * i.rate - i.amount) > 1:
            arith.append({"item_id": i.id, "description": i.description, "qty": i.qty, "rate": i.rate,
                          "amount": i.amount, "expected": round(i.qty * i.rate, 2), "excess": round(i.amount - i.qty * i.rate, 2), "page": i.page})
    seen, dups = {}, []
    for i in b.bill.line_items:
        k = (norm(i.description), round(i.amount, 2), i.date)
        if k in seen:
            dups.append({"item_id": i.id, "duplicate_of": seen[k], "description": i.description, "amount": i.amount, "page": i.page})
        else:
            seen[k] = i.id
    return {"line_sum": ls, "discount": b.bill.discount, "net_of_discount": round(net, 2), "stated_total": stated,
            "unitemized_diff": diff, "arithmetic_errors": arith, "duplicates": dups}


def required_docs(b: Bundle) -> dict:
    cl = kb.checklists().get(b.ctx.jurisdiction) or kb.checklists().get("IN", {})
    labels = kb.checklists().get("labels", {})
    spec = cl.get(b.ctx.mode, {})
    req = list(spec.get("required", []))
    cond = spec.get("conditional", {})
    triggers = []
    if any(i.category == "implant" for i in b.bill.line_items):
        triggers.append("implant")
    if proportionate_check(b)["applicable"] and b.bill.differential_billing is not False:
        triggers.append("proportionate")
    if any(m["unitemized_kit"] for m in non_payable_check(b)["matches"]):
        triggers.append("kit")
    if "accident" in b.clinical_text():
        triggers.append("accident")
    if b.ctx.continuous_coverage_since and b.ctx.continuous_coverage_since != b.ctx.policy_start:
        triggers.append("portability")
    if ped_check(b)["conditions_in_records"]:
        triggers.append("ped")
    for t in triggers:
        req += cond.get(t, [])
    if b.ctx.claim_type == "cashless" and b.ctx.mode == "pre_submission":
        req = [r for r in req if r in ("id_proof", "policy_copy", "claim_form_part_a")]
    have = set(b.ctx.docs_provided)
    missing = [{"id": r, "label": labels.get(r, r), "critical": r in ("final_itemized_bill", "discharge_summary", "claim_form_part_a", "rejection_letter", "investigation_reports"),
                "trigger": next((t for t in triggers if r in cond.get(t, [])), None)} for r in dict.fromkeys(req) if r not in have]
    return {"required": list(dict.fromkeys(req)), "missing": missing, "triggers": triggers,
            "cashless_rule": "IN-DOCS-CASHLESS" if b.ctx.claim_type == "cashless" and b.ctx.jurisdiction == "IN" else None}


def intimation_check(b: Bundle) -> dict:
    h = b.ctx.intimation_hours_after_admission
    lim = b.policy.intimation_emergency_hours
    if h is None or lim is None:
        return {"late": False, "known": False}
    return {"late": h > lim, "known": True, "hours": h, "limit_hours": lim, "regulator_rule": "IN-CONDONE-DELAY" if b.ctx.jurisdiction == "IN" else None}


def exclusion_check(b: Bundle) -> dict:
    text = norm(b.clinical_text())
    hits = []
    for e in b.policy.exclusions:
        kws = e.keywords or []
        m = _cond_match(kws, text) if kws else None
        if m:
            hits.append({"code": e.code, "text": e.text[:300], "matched": m, "cite": e.cite.model_dump()})
    return {"hits": hits}


def tariff_benchmark(b: Bundle) -> dict:
    data = [t for t in kb.tariffs() if not t.get("illustrative")]
    if not data:
        return {"available": False}
    text = norm(b.clinical_text())
    for t in data:
        if _cond_match([t["procedure"], *t.get("aliases", [])], text):
            proc = sum(i.amount for i in b.bill.line_items if i.category in ("surgeon", "ot", "procedure", "package", "anaesthesia"))
            return {"available": True, "procedure": t["procedure"], "scheme": t["scheme"], "rate": t["rate_inr"],
                    "billed_procedure": proc, "ratio": round(proc / t["rate_inr"], 2) if t["rate_inr"] else None}
    return {"available": True, "matched": False}


def insurer_flags(b: Bundle) -> dict:
    """How the insurer actually decided (appeal mode): drives the 'as-is' payout."""
    if b.ctx.mode != "appeal" or not b.rejection:
        return {"ped_reject": False, "generic_reject": False}
    types = {r["issue_type"] for r in rejection_reasons(b)}
    ped = "ped" in types
    generic = b.rejection.decision == "rejected" and not ped and "waiting_period" not in types
    return {"ped_reject": ped, "generic_reject": generic}


def rejection_reasons(b: Bundle) -> list[dict]:
    if not b.rejection:
        return []
    out = []
    for r in b.rejection.reasons:
        t = r.text.lower()
        from core import learned
        it = learned.reason_type(t) or ("ped" if re.search(r"pre.?existing|non.?disclos|misrepresent|ped\b", t) else
              "waiting_period" if "waiting" in t else
              "non_payable" if re.search(r"non.?payable|consumable", t) else
              "proportionate" if re.search(r"proportion|room rent|room category", t) else
              "missing_doc" if re.search(r"document|submit|not received|query|invoice|reports?\b|sticker|provide", t) else
              "intimation" if re.search(r"intimat|delay", t) else
              "exclusion" if "exclu" in t else "other")
        out.append({"text": r.text, "clause": r.clause_cited, "amount": r.amount, "issue_type": it})
    return out
