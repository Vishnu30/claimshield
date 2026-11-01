"""Identify a PDF from its content, not its file name: document kind, insurer, product, UIN.
Lets you drop any downloaded file (e.g. 'download (3).pdf') into any data/raw/ folder."""
from __future__ import annotations
import re
from core.ingest import Doc

INSURERS = {
    "Star Health": ["star health", "star health and allied"], "HDFC ERGO": ["hdfc ergo"], "Niva Bupa": ["niva bupa", "max bupa"],
    "Care Health": ["care health insurance", "religare health"], "ICICI Lombard": ["icici lombard"], "Bajaj Allianz": ["bajaj allianz", "bajaj general"],
    "Aditya Birla Health": ["aditya birla health", "aditya birla"], "Tata AIG": ["tata aig"], "ManipalCigna": ["manipalcigna", "manipal cigna", "cigna ttk"],
    "SBI General": ["sbi general"], "New India Assurance": ["new india assurance"], "United India": ["united india insurance"],
    "Oriental Insurance": ["oriental insurance"], "National Insurance": ["national insurance company"], "Go Digit": ["go digit", "digit general", "digit insurance"],
    "Acko": ["acko general", "acko"], "Reliance General": ["reliance general"], "Future Generali": ["future generali", "generali central"],
    "IFFCO Tokio": ["iffco tokio", "iffco-tokio"], "Royal Sundaram": ["royal sundaram"], "Chola MS": ["cholamandalam"], "Universal Sompo": ["universal sompo"],
    "Liberty General": ["liberty general"], "Zurich Kotak": ["kotak mahindra general", "zurich kotak"], "Magma": ["magma hdi", "magma general"],
    "Raheja QBE": ["raheja qbe"], "Navi General": ["navi general"], "Galaxy Health": ["galaxy health"], "Narayana Health": ["narayana health insurance"],
    "Shriram General": ["shriram general"], "Zuno": ["zuno general", "edelweiss general"],
}
KINDS = {
    "policies": [r"policy wording", r"sum insured", r"waiting period", r"insured person", r"policy schedule", r"exclusions?", r"customer information sheet", r"prospectus", r"\buin\b"],
    "irdai": [r"insurance regulatory and development authority", r"\birdai\b", r"master circular", r"\bcircular\b", r"guidelines", r"ref\.?\s*no", r"all (?:general|health) insurers"],
    "ombudsman": [r"ombudsman", r"\baward\b", r"complainant", r"respondent insurer", r"council for insurance ombudsmen"],
    "tariffs": [r"\bcghs\b", r"package rate", r"health benefit package", r"pm-?jay", r"rate list", r"procedure code", r"nabh"],
    "checklists": [r"claim form", r"part\s*-?\s*a", r"part\s*-?\s*b", r"to be filled by (?:the )?(?:insured|hospital)"],
}
UIN_RX = re.compile(r"\b([A-Z]{3}[A-Z]{2}[A-Z]{2}\d{5}V\d{6}|[A-Z]{5,8}\d{2,5}V\d{5,6})\b")


def _title(doc: Doc) -> str:
    """Largest-font text on page 1 (usually the product name)."""
    try:
        pg = doc.open()[0]
        h = pg.rect.height
        spans = [(s["size"], s["text"].strip()) for b in pg.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l.get("spans", [])
                 if s["text"].strip() and len(s["text"].strip()) > 2 and s["bbox"][1] < h * 0.45]
    except Exception:
        return ""
    if not spans:
        return ""
    top = max(s for s, _ in spans)
    words = list(dict.fromkeys(t for s, t in spans if s >= top - 0.5))[:3]
    return " ".join(" ".join(words).split())[:160]


def clean_product(title: str, insurer: str | None) -> str | None:
    t = re.sub(r"(?i)policy\s*wordings?|customer information sheet|prospectus|terms (?:and|&) conditions|\(synthetic[^)]*\)|[-—–:|]\s*$", " ", title or "")
    if insurer:
        for alias in INSURERS.get(insurer, []):
            t = re.sub(re.escape(alias), " ", t, flags=re.I)
    t = re.sub(r"(?i)\b(?:co(?:mpany)?\.?\s*ltd\.?|limited|insurance company|general insurance|health insurance)\b", " ", t)
    t = " ".join(t.replace("—", " ").split()).strip(" -—–:|,.")
    return t or None


def identify(doc: Doc) -> dict:
    text = " ".join(p.text for p in doc.pages[:3]).lower()
    flat = " ".join(text.split())
    scores = {k: sum(len(re.findall(rx, flat)) for rx in rxs) for k, rxs in KINDS.items()}
    kind = max(scores, key=scores.get) if any(scores.values()) else "unknown"
    if scores.get("policies", 0) and scores.get("irdai", 0) and "uin" in flat and "policy wording" in flat:
        kind = "policies"                        # wordings mention IRDAI everywhere
    insurer = next((name for name, aliases in INSURERS.items() if any(a in flat for a in aliases)), None)
    if not insurer:
        m = re.search(r"(?:insurer|company)\s*[:\-]\s*([A-Z][\w&.\- ]{3,60}?(?:ltd\.?|limited|assurance|insurance))", " ".join(p.text for p in doc.pages[:2]), re.I)
        insurer = " ".join(m.group(1).split()) if m else None
    m = UIN_RX.search(" ".join(p.text for p in doc.pages[:3]))
    title = _title(doc)
    return {"kind": kind, "insurer": insurer, "product": clean_product(title, insurer) if kind == "policies" else None,
            "uin": m.group(1) if m else None, "title": title, "scores": scores, "scanned": doc.scanned}


GENERIC = {"health", "insurance", "policy", "wording", "pdf", "plan", "the", "and", "of", "general", "company", "limited", "ltd", "co"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in GENERIC}


def match_source(info: dict, text: str, rows: list[dict]) -> str | None:
    """Best data/sources.csv row for this file: same category, insurer present, and the most product words in common."""
    t = " ".join((text or "").lower().split()) + " " + (info.get("title") or "").lower()
    ident = _tokens((info.get("product") or "") + " " + (info.get("title") or ""))
    best, best_score = None, 0.0
    for r in rows:
        if r["category"] != info["kind"]:
            continue
        kws = [k for k in (r.get("keywords") or "").split("|") if k]
        if kws and not any(k in t for k in kws):
            continue
        if r["category"] == "policies":
            ins = (info.get("insurer") or "").lower()
            q = (r.get("query") or "").lower()
            if ins and ins.split()[0] not in q:
                continue
            if not ins and " ".join(q.split()[:2]) not in t:
                continue
            prod_words = _tokens(q) - _tokens(ins)
            if not prod_words:
                continue
            overlap = len(prod_words & ident) / len(prod_words)
            if overlap < 0.5:
                continue
            score = overlap + 0.01 * len(prod_words & ident)
        else:
            score = 1.0 if kws else 0.5
        if score > best_score:
            best, best_score = r["id"], score
    return best
