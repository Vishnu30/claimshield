"""S6 Evidence: locate citations on the page and render highlights.
Digital PDFs: PyMuPDF text search (exact, free). Scans / phone photos: Muse vision grounding (normalized bbox), capped + cached."""
from __future__ import annotations
import pymupdf as fitz
import config
from core import llm
from core.ingest import Doc
from core.schemas import Finding

LOCATE_SCHEMA = {"type": "object", "properties": {"found": {"type": "boolean"},
                 "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}}, "required": ["found"]}
LOCATE_PROMPT = ("You locate text on a document image. Find the printed line that best matches the query. "
                 "Return box = [x0, y0, x1, y1] normalized 0-1000 relative to the image width and height (top-left origin). "
                 "If not visible, found=false.")


def _find(doc: Doc, page: int, text: str) -> list[float] | None:
    if not text or not doc.is_pdf or page < 1 or page > len(doc.pages) or doc.pages[page - 1].scanned:
        return None
    pg = doc.open()[page - 1]
    for probe in (text, text[:60], " ".join(text.split()[:6]), " ".join(text.split()[:3])):
        if len(probe) < 3:
            continue
        hits = pg.search_for(probe)
        if hits:
            r = hits[0]
            for h in hits[1:]:
                if abs(h.y0 - r.y0) < 2 or h.y0 - r.y1 < 4:
                    r |= h
            return [r.x0, r.y0, r.x1, r.y1]
    return None


def _vision(doc: Doc, page: int, text: str) -> list[float] | None:
    if page < 1 or page > len(doc.pages):
        return None
    try:
        content = [{"type": "text", "text": f"QUERY: {text[:120]}"},
                   {"type": "image_url", "image_url": {"url": doc.page_data_url(page, 100)}}]
        data, res = llm.complete_json("evidence", LOCATE_PROMPT, content, LOCATE_SCHEMA, label=f"loc:{doc.hash}:{page}:{text[:40]}",
                                      reasoning=False, max_tokens=200)
    except Exception:  # noqa: BLE001
        return None
    box = data.get("box") if isinstance(data, dict) and data.get("found") else None
    if not box or len(box) != 4:
        return None
    pg = doc.pages[page - 1]
    x0, y0, x1, y1 = [max(0.0, min(1000.0, float(v))) for v in box]
    llm.stats().emit({"stage": "evidence", "kind": "info", "text": f"Muse vision located “{text[:40]}” on scanned page {page} via {res['provider']}"})
    return [x0 / 1000 * pg.width, y0 / 1000 * pg.height, x1 / 1000 * pg.width, y1 / 1000 * pg.height]


def locate(findings: list[Finding], docs: dict[str, Doc], bill_items: dict[str, str]) -> None:
    budget = config.MAX_VISION_LOCATES
    for f in sorted(findings, key=lambda x: -x.inr_impact):
        for cite in (f.bill_cite, f.policy_cite):
            if not cite or cite.bbox or not cite.quote:
                continue
            if cite.doc == "policy":
                d = docs.get("policy")
            else:
                d = docs.get(bill_items.get(cite.quote, "")) or next((x for x in docs.values() if x.kind == "bill"), None)
                cite.doc = d.name if d else cite.doc
            if not d:
                continue
            page = cite.page or 1
            box = _find(d, page, cite.quote)
            if box is None and d.scanned and budget > 0 and llm.available():
                box = _vision(d, page, cite.quote); budget -= 1
            if box and cite.doc != "policy" and page <= len(d.pages):     # highlight the whole bill row
                w = d.pages[page - 1].width
                box = [min(36.0, box[0]), box[1], max(w - 36.0, box[2]), box[3]]
            cite.bbox = box


def render(doc: Doc, page: int, boxes: list[list[float]], dpi: int = 120) -> bytes:
    d = doc.open()
    if not doc.is_pdf:                      # photos: convert so annotations work
        d = fitz.open("pdf", d.convert_to_pdf())
    pg = d[max(0, min(page - 1, len(d) - 1))]
    for b in boxes:
        if b:
            r = fitz.Rect(*b) + (-3, -2, 3, 2)
            a = pg.add_rect_annot(r)
            a.set_colors(stroke=(0.85, 0.2, 0.15), fill=(1, 0.85, 0.2))
            a.set_opacity(0.45); a.set_border(width=2); a.update()
    return pg.get_pixmap(dpi=dpi).tobytes("png")
