"""S1 Ingest: PDFs / photos -> pages with text layer, scan detection, page images on demand."""
from __future__ import annotations
import base64
from dataclasses import dataclass, field
import pymupdf as fitz
import config
from core import cache


@dataclass
class Page:
    no: int
    text: str
    scanned: bool
    width: float = 0
    height: float = 0


@dataclass
class Doc:
    name: str
    kind: str                      # policy | bill | discharge | rejection | other
    data: bytes
    pages: list[Page] = field(default_factory=list)
    hash: str = ""
    is_pdf: bool = True

    @property
    def scanned(self) -> bool:
        return any(p.scanned for p in self.pages)

    @property
    def text(self) -> str:
        return "\n".join(f"=== PAGE {p.no} ===\n{p.text}" for p in self.pages)

    def open(self):
        if self.is_pdf:
            return fitz.open(stream=self.data, filetype="pdf")
        return fitz.open(stream=self.data, filetype=self.name.rsplit(".", 1)[-1].lower())

    def page_png(self, no: int, dpi: int | None = None) -> bytes:
        d = self.open()
        pg = d[no - 1]
        return pg.get_pixmap(dpi=dpi or config.PAGE_DPI).tobytes("png")

    def page_data_url(self, no: int, dpi: int | None = None) -> str:
        return "data:image/png;base64," + base64.b64encode(self.page_png(no, dpi)).decode()


MAX_PAGES = {"bill": 40, "discharge": 15, "rejection": 10, "policy": 300}
IMG_EXT = ("png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff")


class InputError(ValueError):
    """User-facing problem with an uploaded file."""


def load(name: str, data: bytes, kind: str, password: str | None = None) -> Doc:
    if not data or len(data) < 64:
        raise InputError(f"{name}: the file is empty.")
    is_pdf = data[:4] == b"%PDF"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not is_pdf and ext not in IMG_EXT:
        if ext in ("heic", "heif"):
            raise InputError(f"{name}: iPhone HEIC photos are not supported — share the photo as JPG (Settings → Camera → Most Compatible).")
        raise InputError(f"{name}: unsupported file type. Upload a PDF or a photo (JPG/PNG).")
    try:
        d = fitz.open(stream=data, filetype="pdf" if is_pdf else ext)
    except Exception:
        raise InputError(f"{name}: the file is damaged or not a real {'PDF' if is_pdf else 'image'}.")
    if is_pdf and d.needs_pass:
        if not password or not d.authenticate(password):
            raise InputError(f"{name}: this PDF is password-protected. Enter its password (often your date of birth or policy number) and try again."
                             if not password else f"{name}: wrong PDF password.")
        data = d.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
        d = fitz.open(stream=data, filetype="pdf")
    if len(d) == 0:
        raise InputError(f"{name}: the PDF has no pages.")
    cap = MAX_PAGES.get(kind, 100)
    if len(d) > cap:
        d.select(list(range(cap)))
        data = d.tobytes()
        d = fitz.open(stream=data, filetype="pdf")
    doc = Doc(name=name, kind=kind, data=data, hash=cache.file_hash(data), is_pdf=is_pdf)
    if is_pdf:
        for i, pg in enumerate(d, start=1):
            t = pg.get_text("text") or ""
            doc.pages.append(Page(no=i, text=t, scanned=len(t.strip()) < 60, width=pg.rect.width, height=pg.rect.height))
    else:
        r = d[0].rect
        doc.pages.append(Page(no=1, text="", scanned=True, width=r.width, height=r.height))
    return doc
