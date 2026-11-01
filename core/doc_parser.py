"""Deterministic fallbacks for bills, discharge summaries and rejection letters (digital PDFs).
Used when no model is reachable or a model answer is unusable, so an upload never dead-ends."""
from __future__ import annotations
import re
from core.ingest import Doc
from core.schemas import Bill, LineItem, Discharge, Rejection, RejectionReason

AMT = r"\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
CATS = [("icu", r"\bicu\b|iccu|intensive care|nicu|picu"), ("room", r"\broom\b|\bward\b|bed charge|day care bed"),
        ("implant", r"implant|stent|\biol\b|lens|mesh|prosthesis|plate|screw|pacemaker|graft"),
        ("surgeon", r"surgeon|surgery fee|operation fee|operating fee|ptca|cardiologist - "), ("anaesthesia", r"an(a)?esth"),
        ("ot", r"\bot\b|operation theat|theatre|cath ?lab"), ("nursing", r"nursing"),
        ("consultation", r"consult|visit|physician|doctor|specialist"),
        ("pharmacy", r"pharmacy|medicine|drug|injection|tablet|iv fluid|antibiotic|syrup"),
        ("investigation", r"\blab\b|test|x-?ray|scan|mri|\bct\b|ultrasound|usg|ecg|echo|biometry|investigation|blood|culture|angiograph|oct\b|lft|cbc"),
        ("consumable", r"consumable|kit|glove|mask|syringe|drape|gauze|cotton|catheter|shoe cover"),
        ("procedure", r"procedure|physio|dressing|dialysis|nebul|transfusion"), ("ambulance", r"ambulance"), ("package", r"package")]
SKIP = re.compile(r"^(?:\W*\d*\s*)?(?:grand total|net (?:payable|amount)|total\b|sub-?total|amount \(rs|advance|amount paid|balance|less:? ?discount|discount|gst|cgst|sgst|igst)", re.I)


def _cat(desc: str) -> str:
    d = desc.lower()
    return next((c for c, rx in CATS if re.search(rx, d)), "misc")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _rows(doc: Doc, page_no: int) -> list[str]:
    """Rebuild visual rows from word coordinates (tables extract cell-by-cell otherwise)."""
    pg = doc.open()[page_no - 1]
    words = pg.get_text("words")
    rows: dict[int, list] = {}
    for x0, y0, x1, y1, w, *_ in words:
        key = round((y0 + y1) / 2 / 3)
        rows.setdefault(key, []).append((x0, w))
    out = []
    for k in sorted(rows):
        out.append(" ".join(w for _, w in sorted(rows[k])))
    return out


def parse_bill(doc: Doc) -> Bill:
    items, stated, discount = [], None, 0.0
    date_rx = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})\b")
    adm = dis = room_cat = hospital = None
    for p in doc.pages:
        if p.scanned:
            continue
        lines = _rows(doc, p.no)
        if p.no == 1 and lines:
            hospital = lines[0][:120]
        for ln in lines:
            low = ln.lower()
            m = re.search(r"admission\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})", low)
            adm = adm or (m.group(1) if m else None)
            m = re.search(r"discharge\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})", low)
            dis = dis or (m.group(1) if m else None)
            m = re.search(r"room category\s*[:\-]?\s*(.+)$", ln, re.I)
            room_cat = room_cat or (m.group(1).strip() if m else None)
            nums = re.findall(AMT, re.sub(date_rx, " ", ln))
            if not nums:
                continue
            if SKIP.search(re.sub(date_rx, " ", low).strip()):
                if re.search(r"grand total|net (?:payable|amount)|^\s*total", low):
                    stated = _num(nums[-1])
                elif "discount" in low:
                    discount = abs(_num(nums[-1]))
                continue
            desc = re.sub(AMT, " ", re.sub(date_rx, " ", ln))
            desc = re.sub(r"^\s*\d+\s+", "", " ".join(desc.split())).strip(" -:|")
            if len(re.sub(r"[^A-Za-z]", "", desc)) < 3 or re.match(r"(uhid|mobile|bill no|patient name|name|date of|page \d)", desc, re.I):
                continue
            amount = _num(nums[-1])
            if amount <= 0:
                continue
            qty = rate = None
            if len(nums) >= 3:
                qty, rate = _num(nums[-3]), _num(nums[-2])
            dm = date_rx.search(ln)
            items.append(LineItem(id=f"L{len(items) + 1:02d}", description=desc[:200], category=_cat(desc), qty=qty or 1, rate=rate,
                                  amount=amount, date=dm.group(1) if dm else None, page=p.no, doc=doc.name))
    room_days = sum(i.qty for i in items if i.category == "room") or None
    icu_days = sum(i.qty for i in items if i.category == "icu") or None
    return Bill(hospital=hospital, admission_date=adm, discharge_date=dis, room_category=room_cat, room_days=room_days, icu_days=icu_days,
                line_items=items, discount=discount, stated_total=stated, source_file=doc.name)


def _field(text: str, *names: str) -> str | None:
    for n in names:
        m = re.search(rf"{n}\s*[:\-]\s*(.+)", text, re.I)
        if m:
            return m.group(1).strip()[:300]
    return None


def parse_discharge(doc: Doc) -> Discharge:
    text = "\n".join(p.text for p in doc.pages)
    at = (_field(text, "admission type", "type of admission") or "").lower()
    como = _field(text, "co-?morbidit(?:y|ies)", "past history", "known case of") or ""
    hist = _field(text, "history", "history of present illness")
    dur = None
    m = re.search(r"(?:for|since)\s*(\d{1,2})\s*(years?|months?)", (hist or "") + " " + como, re.I)
    if m:
        dur = int(m.group(1)) * (12 if m.group(2).lower().startswith("y") else 1)
    return Discharge(diagnosis=_field(text, "final diagnosis", "diagnosis"), procedure=_field(text, "procedure(?:s)?(?:\\s*(?:done|performed))?", "surgery performed", "operation"),
                     icd10=(re.search(r"\b([A-TV-Z]\d{2}(?:\.\d{1,2})?)\b", text) or [None, None])[1],
                     admission_type="emergency" if "emerg" in at else "planned" if "plan" in at else "day_care" if "day" in at else None,
                     comorbidities=[c.strip() for c in re.split(r",|;| and ", como) if c.strip() and c.strip().lower() not in ("none", "nil")][:6],
                     history_notes=hist, condition_duration_months=dur, source_file=doc.name)


def parse_rejection(doc: Doc) -> Rejection:
    text = "\n".join(p.text for p in doc.pages)
    low = text.lower()
    reasons = [RejectionReason(text=m.group(1).strip()[:300], clause_cited=(re.search(r"clause\s*([\w.]+)", m.group(1), re.I) or [None, None])[1])
               for m in re.finditer(r"(?:reason|ground)s?\s*(?:for (?:rejection|repudiation))?\s*[:\-]\s*(.+)", text, re.I)]
    if not reasons:
        m = re.search(r"((?:repudiat|reject|denied|not admissible)[^.\n]{10,250})", text, re.I)
        if m:
            reasons = [RejectionReason(text=m.group(1).strip())]
    amt = lambda *ns: next((_num(m.group(1)) for n in ns for m in [re.search(rf"{n}[^\d\n]{{0,30}}({AMT})", text, re.I)] if m), None)
    dec = (_field(text, "decision") or "").lower()
    if "partial" in dec or "short" in dec:
        decision = "partial"
    elif "query" in dec:
        decision = "query"
    elif dec:
        decision = "rejected"
    elif re.search(r"partial|short|deduct", low) and "repudiat" not in low:
        decision = "partial"
    else:
        decision = "rejected"
    return Rejection(claim_no=_field(text, "claim no\\.?", "claim number"), letter_date=_field(text, "date"), decision=decision,
                     amount_claimed=amt("amount claimed", "claimed amount"), amount_paid=amt("amount approved", "approved amount", "amount paid", "settled amount"),
                     reasons=reasons, source_file=doc.name)
