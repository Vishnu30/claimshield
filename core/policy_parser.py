"""Deterministic policy-wording parser: regex heuristics over the text layer.
Gives every policy a verifiable baseline (each rule carries the exact sentence + page) even with no model;
Muse/Llama extraction then fills gaps and the verifier drops anything whose quote is not in the PDF."""
from __future__ import annotations
import re
from core.schemas import PolicyRules, Limit, Copay, Sublimit, WaitingPeriod, Exclusion, ClauseRef

MONEY = r"(?:rs\.?|inr|₹|rupees)\s*([\d,]+(?:\.\d+)?)"
NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "twelve": 12, "eighteen": 18, "twenty four": 24,
        "twenty-four": 24, "thirty": 30, "thirty six": 36, "thirty-six": 36, "forty eight": 48, "forty-eight": 48}


ABBR = re.compile(r"(?:\b(?:Rs|rs|No|no|Sr|Dr|Mr|Mrs|viz|i\.e|e\.g|etc|Co|Ltd|approx)\.)$")


def sentences(pages: list[str]) -> list[tuple[int, str]]:
    """Sentence split that keeps 'Rs. 5,000' together and glues short headings ('3.5 Co-payment.') to the next sentence."""
    out = []
    for no, t in enumerate(pages, 1):
        flat = " ".join((t or "").split())
        parts, buf = [], ""
        for piece in re.split(r"(?<=[.;])\s+(?=[A-Z(\d])", flat):
            buf = f"{buf} {piece}".strip() if buf else piece
            if ABBR.search(buf) or len(buf) < 60:
                continue
            parts.append(buf); buf = ""
        if buf:
            parts.append(buf)
        out += [(no, x) for x in parts if 25 <= len(x) <= 1200]
    return out


def _num(s: str) -> float | None:
    try:
        return float((s or "").replace(",", "")) if re.search(r"\d", s or "") else None
    except ValueError:
        return None


def _months(sent: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*(?:\(\s*[a-z\- ]+\)\s*)?(months?|years?)", sent, re.I)
    if m:
        v = int(m.group(1)); return v * 12 if m.group(2).lower().startswith("year") else v
    for w, v in NUMW.items():
        m = re.search(rf"\b{w}\b\s*(months?|years?)", sent, re.I)
        if m:
            return v * 12 if m.group(1).lower().startswith("year") else v
    return None


def _ref(page: int, sent: str, cid: str | None = None) -> ClauseRef:
    m = re.match(r"^\s*((?:\d+(?:\.\d+){0,3})|(?:Excl\s?\d+)|(?:[A-Z]\.\d+))\b", sent)
    return ClauseRef(clause_id=cid or (m.group(1).replace(" ", "") if m else None), page=page, quote=sent[:600])


def _limit(sents, words: str, avoid: str | None = None) -> Limit:
    for page, s in sents:
        low = s.lower()
        if not re.search(words, low) or (avoid and re.search(avoid, low)):
            continue
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s*(?:the\s*)?)?(?:sum insured|si\b)", low)
        cap = re.search(r"(?:max(?:imum)?|subject to|up ?to|capped at)[^.]{0,40}?" + MONEY, low)
        if pct:
            return Limit(kind="pct_si", value=float(pct.group(1)), max_value=_num(cap.group(1)) if cap else None, cite=_ref(page, s))
        fixed = re.search(r"(?:up ?to|maximum of|limited to)\s*" + MONEY + r"[^.]{0,30}?(?:per day|/day|a day|per diem)", low)
        if fixed:
            return Limit(kind="fixed", value=_num(fixed.group(1)), cite=_ref(page, s))
        cat = re.search(r"(single private (?:a\.?c\.? )?room|shared room|twin sharing|general ward|any room)", low)
        if cat and "room" in words:
            return Limit(kind="category" if "any room" not in cat.group(1) else "none", category=cat.group(1), cite=_ref(page, s))
    return Limit()


def parse(pages: list[str], policy_id: str, jurisdiction: str = "IN") -> PolicyRules:
    sents = sentences(pages)
    low_all = " ".join(s.lower() for _, s in sents)
    pol = PolicyRules(policy_id=policy_id, jurisdiction=jurisdiction)
    m = re.search(r"\b(?:uin|unique identification (?:number|no\.?))\s*[:\-]?\s*([A-Z]{5}[A-Z0-9]{10,20})", " ".join(pages), re.I)
    pol.uin = m.group(1).upper() if m else None
    pol.room_rent = _limit(sents, r"room rent|room,? boarding|room charges", r"intensive|icu|iccu")
    pol.icu_rent = _limit(sents, r"\bicu\b|intensive care|iccu")
    for page, s in sents:
        low = s.lower()
        if re.search(r"proportionate|ratabl|in the same proportion", low) and "room" in low:
            pol.proportionate_deduction, pol.proportionate_cite = True, _ref(page, s); break
    else:
        pol.proportionate_deduction = "proportion" in low_all
    seen_cp = set()
    for page, s in sents:
        low = s.lower()
        if re.search(r"co-?pay", low):
            pct = re.search(r"(\d{1,2})\s*%", low)
            if pct and pct.group(1) not in seen_cp:
                age = re.search(r"(\d{2})\s*years?\s*(?:of age\s*)?(?:and|or)\s*(?:above|more|older)", low)
                pol.copays.append(Copay(pct=float(pct.group(1)), condition=s[:120], age_min=int(age.group(1)) if age else None, cite=_ref(page, s)))
                seen_cp.add(pct.group(1))
    for proc, aliases in (("cataract", ["phacoemulsification", "phaco", "iol"]), ("joint replacement", ["knee replacement", "hip replacement"]),
                          ("modern treatment", ["robotic surgery", "oral chemotherapy", "immunotherapy"])):
        for page, s in sents:
            low = s.lower()
            if proc.split()[0] in low and re.search(MONEY + "|%", low) and re.search(r"limit|up ?to|maximum|lower|capped|restricted", low):
                amt = re.search(MONEY, low); pct = re.search(r"(\d{1,2})\s*%\s*of\s*(?:the\s*)?sum insured", low)
                pol.sublimits.append(Sublimit(procedure=proc, aliases=aliases, limit=_num(amt.group(1)) if amt else None,
                                              pct_si=float(pct.group(1)) if pct else None, basis="per_eye" if "eye" in low else "per_claim", cite=_ref(page, s)))
                break
    # waiting periods
    for page, s in sents:
        low = s.lower()
        if re.search(r"first\s*(?:30|thirty)\s*days|30\s*days\s*(?:from|of)\s*(?:the\s*)?(?:first|inception|commencement)", low):
            pol.waiting_periods.append(WaitingPeriod(kind="initial", months=1, cite=_ref(page, s))); break
    for page, s in sents:
        low = s.lower()
        if re.search(r"pre-?existing", low) and re.search(r"months|years", low) and re.search(r"exclu|waiting|covered after|until", low):
            mo = _months(s)
            if mo and 6 <= mo <= 60:
                pol.waiting_periods.append(WaitingPeriod(kind="ped", months=mo, cite=_ref(page, s))); break
    for idx, (page, s) in enumerate(sents):
        low = s.lower()
        if re.search(r"specified|specific|listed|following", low) and re.search(r"waiting|exclu", low) and re.search(r"months|years", low) and "pre-existing" not in low:
            mo = _months(s)
            if not mo or not 6 <= mo <= 60:
                continue
            window = " ".join(x for _, x in sents[idx: idx + 8]).lower()
            known = ["cataract", "hernia", "hydrocele", "benign prostatic", "hysterectomy", "fibroid", "joint replacement", "knee replacement",
                     "gall bladder", "cholelithiasis", "kidney stone", "calculus", "sinusitis", "tonsil", "adenoid", "piles", "haemorrhoid", "fissure",
                     "fistula", "varicose", "osteoarthritis", "spondylosis", "disc", "glaucoma", "septum", "polycystic", "dialysis", "cholecystectomy"]
            conds = [k for k in known if k in window]
            if conds:
                pol.waiting_periods.append(WaitingPeriod(kind="specific", months=mo, conditions=conds, cite=_ref(page, s))); break
    for page, s in sents:
        m = re.search(r"(Excl\s?\d{2})\b\s*[:.\-]?\s*(.{10,240})", s)
        if m and not any(e.code == m.group(1).replace(" ", "") for e in pol.exclusions):
            text = m.group(2)
            kws = [w for w in ("cosmetic", "plastic surgery", "obesity", "bariatric", "infertility", "maternity", "dental", "spectacles",
                               "alcohol", "self-inflicted", "war", "hazardous", "experimental", "rest cure", "change of gender") if w in text.lower()]
            pol.exclusions.append(Exclusion(code=m.group(1).replace(" ", ""), text=text, keywords=kws, cite=_ref(page, s, m.group(1).replace(" ", ""))))
    for page, s in sents:
        low = s.lower()
        h = re.search(r"within\s*(\d{1,3})\s*hours?\s*of\s*(?:the\s*|an?\s*)?(?:emergency\s*)?(?:hospitali[sz]ation|admission)", low)
        if h and pol.intimation_emergency_hours is None:
            pol.intimation_emergency_hours = int(h.group(1))
        d = re.search(r"within\s*(\d{1,3})\s*days\s*(?:from|of)\s*(?:the\s*)?(?:date of\s*)?discharge", low)
        if d and pol.claim_submission_days is None:
            pol.claim_submission_days = int(d.group(1))
    return pol


def merge(base: PolicyRules, llm: PolicyRules) -> PolicyRules:
    """LLM values fill gaps; for scalar limits a cited LLM value wins over a heuristic one."""
    out = base.model_copy(deep=True)
    for f in ("insurer", "product", "uin", "sum_insured", "intimation_emergency_hours", "intimation_planned_days_before", "claim_submission_days"):
        if getattr(out, f) in (None, "") and getattr(llm, f) not in (None, ""):
            setattr(out, f, getattr(llm, f))
    for f in ("room_rent", "icu_rent"):
        a, b = getattr(out, f), getattr(llm, f)
        if b.kind != "none" and (a.kind == "none" or (b.cite.quote and not a.cite.quote)):
            setattr(out, f, b)
    if llm.proportionate_cite.quote and not out.proportionate_cite.quote:
        out.proportionate_deduction, out.proportionate_cite = llm.proportionate_deduction, llm.proportionate_cite
    for f, key in (("copays", lambda x: x.pct), ("sublimits", lambda x: x.procedure.lower()), ("waiting_periods", lambda x: (x.kind, x.months)),
                   ("exclusions", lambda x: (x.code or x.text[:30]).lower())):
        have = {key(x) for x in getattr(out, f)}
        for x in getattr(llm, f):
            if key(x) not in have:
                getattr(out, f).append(x); have.add(key(x))
    # a specific waiting period with more conditions from the model replaces the heuristic one of the same length
    for w in llm.waiting_periods:
        for i, b in enumerate(out.waiting_periods):
            if b.kind == w.kind == "specific" and b.months == w.months and len(w.conditions) > len(b.conditions):
                out.waiting_periods[i] = w
    return out
