"""S2 Extract: Muse vision/text -> typed JSON. Hash-cached, PII-redacted, sidecar fallback for zero-credit demo."""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Optional
import config
from config import PATHS
from core import llm, kb, prompts, cache
from core.ingest import Doc
from core.privacy import Vault
from core.schemas import Bill, LineItem, Discharge, Rejection, PolicyRules, Limit, Copay, Sublimit, WaitingPeriod, Exclusion, ClauseRef

CAT_ENUM = ["room", "icu", "nursing", "consultation", "surgeon", "anaesthesia", "ot", "procedure", "implant", "pharmacy",
            "consumable", "investigation", "package", "ambulance", "misc"]
NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}
BILL_SCHEMA = {"type": "object", "properties": {
    "hospital": STR, "bill_no": STR, "admission_date": STR, "discharge_date": STR, "room_category": STR, "room_days": NUM, "icu_days": NUM,
    "stated_total": NUM, "discount": NUM, "package": {"type": ["boolean", "null"]}, "differential_billing": {"type": ["boolean", "null"]},
    "line_items": {"type": "array", "items": {"type": "object", "properties": {
        "description": {"type": "string"}, "category": {"type": "string", "enum": CAT_ENUM}, "qty": NUM, "rate": NUM,
        "amount": {"type": "number"}, "date": STR, "page": {"type": ["integer", "null"]}}, "required": ["description", "amount"]}}},
    "required": ["line_items"]}
DIS_SCHEMA = {"type": "object", "properties": {"diagnosis": STR, "procedure": STR, "icd10": STR,
              "admission_type": {"type": ["string", "null"], "enum": ["emergency", "planned", "day_care", None]},
              "comorbidities": {"type": "array", "items": {"type": "string"}}, "history_notes": STR, "condition_duration_months": {"type": ["integer", "null"]}}}
REJ_SCHEMA = {"type": "object", "properties": {"insurer": STR, "letter_date": STR, "claim_no": STR,
              "decision": {"type": "string", "enum": ["rejected", "partial", "query"]}, "amount_claimed": NUM, "amount_paid": NUM,
              "reasons": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "clause_cited": STR, "amount": NUM}, "required": ["text"]}}}}
CITE = {"type": "object", "properties": {"clause_id": STR, "page": {"type": ["integer", "null"]}, "quote": STR}}
LIMIT = {"type": "object", "properties": {"kind": {"type": "string", "enum": ["pct_si", "fixed", "category", "none"]}, "value": NUM, "max_value": NUM, "category": STR, "cite": CITE}}
POLICY_SCHEMA = {"type": "object", "properties": {
    "insurer": STR, "product": STR, "uin": STR, "sum_insured": NUM, "room_rent": LIMIT, "icu_rent": LIMIT,
    "proportionate_deduction": {"type": ["boolean", "null"]}, "proportionate_cite": CITE,
    "copays": {"type": "array", "items": {"type": "object", "properties": {"pct": {"type": "number"}, "condition": STR, "age_min": {"type": ["integer", "null"]}, "cite": CITE}}},
    "sublimits": {"type": "array", "items": {"type": "object", "properties": {"procedure": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}}, "limit": NUM, "pct_si": NUM, "basis": STR, "cite": CITE}}},
    "waiting_periods": {"type": "array", "items": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["initial", "specific", "ped"]}, "months": {"type": "integer"}, "conditions": {"type": "array", "items": {"type": "string"}}, "cite": CITE}}},
    "exclusions": {"type": "array", "items": {"type": "object", "properties": {"code": STR, "text": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}}, "cite": CITE}}},
    "intimation_emergency_hours": {"type": ["integer", "null"]}, "intimation_planned_days_before": {"type": ["integer", "null"]}, "claim_submission_days": {"type": ["integer", "null"]}}}


# ---------------------------------------------------------------- sidecar (zero-credit) lookup
_SIDE: dict[str, tuple[str, Path]] = {}


def _side_index() -> dict[str, tuple[str, "Path"]]:
    if not _SIDE:
        for d in PATHS["scenarios"].glob("*"):
            if (d / "truth.json").exists():
                for f in d.iterdir():
                    if f.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png") and f.is_file():
                        _SIDE[cache.file_hash(f.read_bytes())] = (f.name, d)
    return _SIDE


def sidecar(doc: Doc) -> Optional[dict]:
    hit = _side_index().get(doc.hash)
    if not hit:
        return None
    fname, d = hit
    t = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    parts = t.get("bill_parts") or {}
    if fname in parts:                       # multi-bill scenario: return only this file's lines
        t = {**t, "bill": parts[fname]}
    return t


def _payload(doc: Doc, vault: Vault, instruction: str, max_pages: int = 8, pages: Optional[list[int]] = None) -> list | str:
    sel = [p for p in doc.pages if (pages is None or p.no in pages)][:max_pages]
    if not doc.scanned:  # text layer: ~10x cheaper than images
        body = "\n".join(f"=== PAGE {p.no} ===\n{vault.redact(p.text) if config.REDACT_PII else p.text}" for p in sel)
        return f"{instruction}\n\nDOCUMENT ({doc.kind}, {len(doc.pages)} pages):\n{body}"
    parts: list = [{"type": "text", "text": f"{instruction}\nDocument: {doc.kind}, pages {[p.no for p in sel]}."}]
    for p in sel:
        parts.append({"type": "text", "text": f"=== PAGE {p.no} ==="})
        parts.append({"type": "image_url", "image_url": {"url": doc.page_data_url(p.no)}})
    return parts


def _emit(stage, msg, **kw):
    llm.stats().emit({"stage": stage, "kind": "info", "text": msg, **kw})


class NoModel(Exception):
    pass


def _fallback_bill(doc: Doc, sc: Optional[dict], why: str) -> Bill:
    from core import doc_parser
    if sc:
        _emit("extract", f"bill: {why} → ground-truth sidecar")
        bl = Bill.model_validate(sc["bill"])
    elif not doc.scanned:
        bl = doc_parser.parse_bill(doc)
        _emit("extract", f"bill: {why} → deterministic table parser read {len(bl.line_items)} line(s)")
    else:
        raise ValueError(f"{doc.name}: this is a scanned image and no vision model is reachable right now. "
                         "Try again when online, or upload the hospital's digital (PDF) bill.")
    for it in bl.line_items:
        it.doc = doc.name
    return bl


def extract_bill(doc: Doc, vault: Vault) -> Bill:
    sc = sidecar(doc)
    if sc and (config.USE_SIDECAR or config.LLM_MODE == "off" or not llm.available()):
        return _fallback_bill(doc, sc, "demo scenario")
    if config.LLM_MODE == "off" or not llm.available():
        return _fallback_bill(doc, None, "offline")
    per = 3 if doc.scanned else 6
    pages = [p.no for p in doc.pages][:30]
    merged: dict = {"line_items": []}
    used = None
    try:
        for i in range(0, len(pages), per):
            chunk = pages[i:i + per]
            data, res = llm.complete_json("extract", prompts.EXTRACT_BILL, _payload(doc, vault, "Extract the bill.", per, chunk), BILL_SCHEMA,
                                          label=f"bill:{doc.hash}:{chunk[0]}", max_tokens=6000)
            data = data if isinstance(data, dict) else {}
            used = res["provider"]
            for k, v in data.items():
                if k == "line_items":
                    merged["line_items"] += [x for x in (v or []) if isinstance(x, dict)]
                elif v not in (None, "", [], 0) and (merged.get(k) in (None, "", 0) or k == "stated_total"):
                    merged[k] = v
    except (llm.LLMUnavailable, ValueError) as e:
        return _fallback_bill(doc, sc, f"model unavailable ({str(e)[:60]})")
    if not merged["line_items"]:
        bl = _fallback_bill(doc, sc, "model found no lines") if (sc or not doc.scanned) else Bill()
        if bl.line_items:
            return bl
    data = merged
    _emit("extract", f"bill: {len(data['line_items'])} lines via {used}")
    items = []
    for n, it in enumerate(data.get("line_items", []), 1):
        cat = it.get("category") if it.get("category") in CAT_ENUM else "misc"
        try:
            amount = float(str(it.get("amount") or 0).replace(",", ""))
        except ValueError:
            continue
        items.append(LineItem(id=f"L{n:02d}", description=str(it.get("description", ""))[:200], category=cat, qty=it.get("qty") or 1,
                              rate=it.get("rate"), amount=amount, date=it.get("date"), page=it.get("page") or 1, doc=doc.name))
    num = lambda v: float(str(v).replace(",", "")) if v not in (None, "") else None
    return Bill(hospital=data.get("hospital"), bill_no=data.get("bill_no"), admission_date=data.get("admission_date"),
                discharge_date=data.get("discharge_date"), room_category=data.get("room_category"), room_days=num(data.get("room_days")),
                icu_days=num(data.get("icu_days")), line_items=items, discount=num(data.get("discount")) or 0, stated_total=num(data.get("stated_total")),
                package=bool(data.get("package")), differential_billing=data.get("differential_billing"), source_file=doc.name)


def merge_bills(bills: list[Bill]) -> Bill:
    if len(bills) == 1:
        return bills[0]
    base = bills[0].model_copy(deep=True)
    n = len(base.line_items)
    for b in bills[1:]:
        for it in b.line_items:
            n += 1
            base.line_items.append(it.model_copy(update={"id": f"L{n:02d}"}))
        base.discount += b.discount
        if base.stated_total is not None and b.stated_total is not None:
            base.stated_total += b.stated_total
        base.room_days = (base.room_days or 0) + (b.room_days or 0) or None
    return base


def extract_discharge(doc: Doc, vault: Vault) -> Discharge:
    from core import doc_parser
    sc = sidecar(doc)
    if sc and (config.USE_SIDECAR or config.LLM_MODE == "off" or not llm.available()):
        _emit("extract", "discharge: ground-truth sidecar used"); return Discharge.model_validate(sc["discharge"])
    if (config.LLM_MODE == "off" or not llm.available()) and not doc.scanned:
        _emit("extract", "discharge: offline → deterministic parser"); return doc_parser.parse_discharge(doc)
    try:
        data, res = llm.complete_json("extract", prompts.EXTRACT_DISCHARGE, _payload(doc, vault, "Extract the discharge summary.", 4), DIS_SCHEMA, label=f"dis:{doc.hash}")
    except (llm.LLMUnavailable, ValueError):
        if sc:
            return Discharge.model_validate(sc["discharge"])
        if not doc.scanned:
            _emit("extract", "discharge: model unavailable → deterministic parser"); return doc_parser.parse_discharge(doc)
        _emit("extract", "discharge: unreadable scan without a model → skipped"); return Discharge()
    _emit("extract", f"discharge: {data.get('diagnosis')} via {res['provider']}")
    data = data if isinstance(data, dict) else {}
    if data.get("admission_type") not in ("emergency", "planned", "day_care"):
        data.pop("admission_type", None)
    data["comorbidities"] = [str(c) for c in data.get("comorbidities") or [] if c]
    try:
        return Discharge.model_validate({**{k: v for k, v in data.items() if v is not None}, "source_file": doc.name})
    except Exception:  # noqa: BLE001
        return doc_parser.parse_discharge(doc) if not doc.scanned else Discharge()


def extract_rejection(doc: Doc, vault: Vault) -> Rejection:
    from core import doc_parser
    sc = sidecar(doc)
    if sc and sc.get("rejection") and (config.USE_SIDECAR or config.LLM_MODE == "off" or not llm.available()):
        _emit("extract", "rejection: ground-truth sidecar used"); return Rejection.model_validate(sc["rejection"])
    if (config.LLM_MODE == "off" or not llm.available()) and not doc.scanned:
        _emit("extract", "rejection: offline → deterministic parser"); return doc_parser.parse_rejection(doc)
    try:
        data, res = llm.complete_json("extract", prompts.EXTRACT_REJECTION, _payload(doc, vault, "Extract the letter.", 4), REJ_SCHEMA, label=f"rej:{doc.hash}")
    except (llm.LLMUnavailable, ValueError):
        if sc and sc.get("rejection"):
            return Rejection.model_validate(sc["rejection"])
        if not doc.scanned:
            _emit("extract", "rejection: model unavailable → deterministic parser"); return doc_parser.parse_rejection(doc)
        raise ValueError(f"{doc.name}: scanned letter cannot be read without a model right now.")
    _emit("extract", f"rejection: {len(data.get('reasons', []))} reason(s) via {res['provider']}")
    data = {k: v for k, v in (data if isinstance(data, dict) else {}).items() if v is not None}
    if data.get("decision") not in ("rejected", "partial", "query"):
        data["decision"] = "rejected"
    data["reasons"] = [r for r in data.get("reasons", []) if isinstance(r, dict) and r.get("text")]
    return Rejection.model_validate({**data, "source_file": doc.name})


FOCUS = [
    ("limits", ["room rent", "room charges", "icu", "intensive care", "proportionate", "co-pay", "copay", "co-payment", "sub-limit", "sublimit",
                "cataract", "sum insured", "limit", "per day"],
     ["insurer", "product", "uin", "sum_insured", "room_rent", "icu_rent", "proportionate_deduction", "proportionate_cite", "copays", "sublimits"],
     "Focus ONLY on: insurer/product/UIN, sum insured, room rent and ICU limits, proportionate deduction, co-payment, sub-limits."),
    ("waiting", ["waiting period", "pre-existing", "exclu", "excl", "moratorium", "specified disease", "months of continuous"],
     ["waiting_periods", "exclusions"],
     "Focus ONLY on: initial / specific-disease / pre-existing waiting periods (with every listed condition) and the exclusions list."),
    ("claims", ["intimat", "claim", "document", "discharge", "hours", "reimbursement", "cashless"],
     ["intimation_emergency_hours", "intimation_planned_days_before", "claim_submission_days"],
     "Focus ONLY on: claim intimation deadlines and the reimbursement submission deadline."),
]


def select_pages(doc: Doc, keys: list[str], k: int) -> list[int]:
    scored = sorted(((sum(p.text.lower().count(w) for w in keys), p.no) for p in doc.pages), reverse=True)
    picked = [no for s, no in scored if s > 0][:k]
    return sorted(picked or [p.no for p in doc.pages[:k]])


def _verified(ref: ClauseRef, texts: dict[int, str]) -> bool:
    from rapidfuzz import fuzz
    if not ref or not ref.quote:
        return False
    q = " ".join(ref.quote.split())[:300]
    if ref.page and ref.page in texts and fuzz.partial_ratio(q, texts[ref.page]) >= 88:
        return True
    for no, t in texts.items():
        if fuzz.partial_ratio(q, t) >= 88:
            ref.page = no
            return True
    return False


def _keep_verified(p: PolicyRules, texts: dict[int, str]) -> PolicyRules:
    if not texts:                      # scanned policy: nothing to verify against; keep model output
        return p
    for f in ("room_rent", "icu_rent"):
        lim = getattr(p, f)
        if lim.kind != "none" and not _verified(lim.cite, texts):
            setattr(p, f, Limit())
    if p.proportionate_cite.quote and not _verified(p.proportionate_cite, texts):
        p.proportionate_cite = ClauseRef()
    p.copays = [c for c in p.copays if _verified(c.cite, texts)]
    p.sublimits = [x for x in p.sublimits if _verified(x.cite, texts)]
    p.waiting_periods = [w for w in p.waiting_periods if _verified(w.cite, texts)]
    p.exclusions = [e for e in p.exclusions if _verified(e.cite, texts) or not e.cite.quote]
    return p


def _needed(base: PolicyRules) -> set[str]:
    """Which focused model passes are still worth paying for after the deterministic parser."""
    need = set()
    if base.room_rent.kind == "none" or base.icu_rent.kind == "none" or not (base.insurer and base.product):
        need.add("limits")
    if {w.kind for w in base.waiting_periods} != {"initial", "specific", "ped"} or not base.exclusions:
        need.add("waiting")
    if base.intimation_emergency_hours is None or base.claim_submission_days is None:
        need.add("claims")
    return need


def extract_policy(doc: Doc, vault: Vault, jurisdiction: str = "IN", role: str = "extract", policy_id: str | None = None,
                   save: bool = True, fast: bool = False, known: dict | None = None) -> PolicyRules:
    """Deterministic parser (always) → model passes ONLY for what the parser missed → merge → keep rules whose quote is in the PDF."""
    from core import policy_parser
    pid = policy_id or f"upl_{doc.hash}"
    path = PATHS["policies"] / f"{pid}.json"
    if save and path.exists():
        cached = kb.load_policy(pid)
        if cached.room_rent.kind != "none" or cached.waiting_periods or cached.copays or cached.exclusions:
            _emit("extract", "policy: already in library (0 credits)")
            return cached
    texts = {p.no: " ".join(p.text.split()) for p in doc.pages if not p.scanned}
    base = policy_parser.parse([p.text for p in doc.pages], pid, jurisdiction)
    for k in ("insurer", "product", "uin"):
        if (known or {}).get(k) and not getattr(base, k):
            setattr(base, k, known[k])                     # identity already read from page 1 → no model pass for it
    need = set() if fast else _needed(base)
    _emit("extract", f"policy: parser found room={base.room_rent.kind}, {len(base.waiting_periods)} waiting, {len(base.copays)} co-pay, "
                     f"{len(base.exclusions)} exclusions → model passes needed: {sorted(need) or 'none'}")
    got = PolicyRules(policy_id=pid, jurisdiction=jurisdiction)
    text_pages = [p for p in doc.pages if not p.scanned]
    if need and len(text_pages) < max(1, len(doc.pages) // 2):           # mostly scanned → one combined vision pass on the first pages
        vrole = "kb_vision" if role == "kb" else role
        pages = [p.no for p in doc.pages][:6]
        for i in range(0, len(pages), 2):
            chunk = pages[i:i + 2]
            try:
                data, res = llm.complete_json(vrole, prompts.EXTRACT_POLICY, _payload(doc, Vault(), "Extract the policy rules.", 2, chunk),
                                              POLICY_SCHEMA, label=f"pol:{doc.hash}:scan:{chunk}", max_tokens=3000)
                got = policy_parser.merge(got, policy_from_llm(data, pid, jurisdiction))
                _emit("extract", f"policy scan p{chunk} via {res['provider']}")
            except (llm.LLMUnavailable, ValueError) as e:
                _emit("extract", f"policy scan p{chunk}: no vision model available ({str(e)[:60]})"); break
    else:
        for name, keys, fields, focus in FOCUS:
            if name not in need:
                continue
            pages = select_pages(doc, keys, min(config.MAX_POLICY_PAGES_TO_LLM, 4))
            schema = {"type": "object", "properties": {k: POLICY_SCHEMA["properties"][k] for k in fields}}
            for i in range(0, len(pages), 2):
                chunk = pages[i:i + 2]
                try:
                    data, res = llm.complete_json(role, prompts.EXTRACT_POLICY + "\n" + focus + "\nUse plain strings and numbers, never nested objects for single values.",
                                                  _payload(doc, Vault(), f"Extract ({name}).", 2, chunk), schema,
                                                  label=f"pol:{doc.hash}:{name}:{chunk}", max_tokens=2500)
                except (llm.LLMUnavailable, ValueError) as e:
                    _emit("extract", f"policy {name} p{chunk}: model unavailable → parser result kept ({str(e)[:60]})")
                    break
                try:
                    part = _keep_verified(policy_from_llm(data, pid, jurisdiction), texts)
                    got = policy_parser.merge(got, part)
                    _emit("extract", f"policy {name} p{chunk} via {res['provider']}{' (cache)' if res.get('cached') else ''}")
                except Exception as e:  # noqa: BLE001
                    _emit("extract", f"policy {name} p{chunk}: unusable answer skipped ({str(e)[:60]})")
    pol = policy_parser.merge(base, got)
    pol.policy_id, pol.jurisdiction = pid, jurisdiction
    pol.clauses = kb.split_clauses([pg.text for pg in doc.pages]) if text_pages else []
    pol.source_file = doc.name
    if save and (pol.room_rent.kind != "none" or pol.waiting_periods or pol.copays or pol.exclusions):
        kb.save_policy(pol)                                  # never cache an unreadable result — retry when a model is available
    _emit("extract", f"policy ready: room={pol.room_rent.kind} {pol.room_rent.value}, {len(pol.waiting_periods)} waiting, "
                     f"{len(pol.copays)} co-pay, {len(pol.sublimits)} sub-limit, {len(pol.exclusions)} exclusions, {len(pol.clauses)} clauses")
    return pol


# ---------------------------------------------------------------- tolerant coercion (small local models return odd shapes)
def _unwrap(v):
    while isinstance(v, dict):
        v = next((v[k] for k in ("value", "text", "name", "amount", "quote", "id") if k in v), None)
    if isinstance(v, list):
        v = v[0] if v else None
    return v


def S(v, n: int = 300):
    v = _unwrap(v)
    if v is None or isinstance(v, bool):
        return None
    t = " ".join(str(v).split())
    return t[:n] or None


def N(v):
    v = _unwrap(v)
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def I(v):
    x = N(v)
    return int(x) if x is not None else None


def B(v):
    v = _unwrap(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return {"true": True, "yes": True, "false": False, "no": False}.get(v.strip().lower())
    return None


def LD(v):
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else ([v] if isinstance(v, dict) and v else [])


def LS(v):
    if isinstance(v, str):
        return [x.strip() for x in re.split(r",|;|\n", v) if x.strip()][:60]
    return [s for s in (S(x, 120) for x in (v if isinstance(v, list) else [])) if s][:60]


def _cite(d) -> ClauseRef:
    if not isinstance(d, dict):
        return ClauseRef(quote=S(d, 600)) if isinstance(d, str) else ClauseRef()
    return ClauseRef(clause_id=S(d.get("clause_id"), 40), page=I(d.get("page")), quote=S(d.get("quote"), 600))


def policy_from_llm(d: dict, pid: str, jurisdiction: str) -> PolicyRules:
    d = d if isinstance(d, dict) else {}

    def lim(x):
        x = x if isinstance(x, dict) else {}
        kind = S(x.get("kind"))
        kind = kind if kind in ("pct_si", "fixed", "category", "none") else ("pct_si" if N(x.get("value")) and N(x.get("value")) <= 10 else "none")
        cite = _cite(x.get("cite")) if x.get("cite") else ClauseRef(quote=S(x.get("quote"), 600), page=I(x.get("page")))
        return Limit(kind=kind, value=N(x.get("value")), max_value=N(x.get("max_value")), category=S(x.get("category"), 80), cite=cite)

    def each(items, build):
        out = []
        for it in LD(items):
            try:
                o = build(it)
                if o is not None:
                    out.append(o)
            except Exception:  # noqa: BLE001 — one malformed item never sinks the policy
                continue
        return out

    def cite_of(it):
        return _cite(it.get("cite")) if it.get("cite") else ClauseRef(clause_id=S(it.get("clause_id"), 40), page=I(it.get("page")), quote=S(it.get("quote"), 600))

    copays = each(d.get("copays"), lambda c: Copay(pct=N(c.get("pct")), condition=S(c.get("condition"), 200) or "all claims", age_min=I(c.get("age_min")), cite=cite_of(c))
                  if N(c.get("pct")) and 0 < N(c.get("pct")) < 100 else None)
    subs = each(d.get("sublimits"), lambda x: Sublimit(procedure=S(x.get("procedure"), 80), aliases=LS(x.get("aliases")), limit=N(x.get("limit")),
                                                      pct_si=N(x.get("pct_si")), basis=S(x.get("basis"), 30) or "per_claim", cite=cite_of(x)) if S(x.get("procedure")) else None)
    waits = each(d.get("waiting_periods"), lambda w: WaitingPeriod(kind=S(w.get("kind")), months=I(w.get("months")), conditions=LS(w.get("conditions")), cite=cite_of(w))
                 if S(w.get("kind")) in ("initial", "specific", "ped") and I(w.get("months")) and 0 < I(w.get("months")) <= 72 else None)
    excls = each(d.get("exclusions"), lambda e: Exclusion(code=S(e.get("code"), 20), text=S(e.get("text"), 400), keywords=LS(e.get("keywords")), cite=cite_of(e))
                 if S(e.get("text")) else None)
    return PolicyRules(
        policy_id=pid, insurer=S(d.get("insurer"), 120), product=S(d.get("product"), 160), uin=S(d.get("uin"), 40), jurisdiction=jurisdiction,
        sum_insured=N(d.get("sum_insured")), room_rent=lim(d.get("room_rent")), icu_rent=lim(d.get("icu_rent")),
        proportionate_deduction=B(d.get("proportionate_deduction")) is not False, proportionate_cite=_cite(d.get("proportionate_cite")),
        copays=copays, sublimits=subs, waiting_periods=waits, exclusions=excls,
        intimation_emergency_hours=I(d.get("intimation_emergency_hours")), intimation_planned_days_before=I(d.get("intimation_planned_days_before")),
        claim_submission_days=I(d.get("claim_submission_days")))
