"""HTML → PDF with PyMuPDF's layout engine: complex-script shaping (Devanagari, Tamil, Bengali …) and ₹ render correctly,
unlike core PDF fonts that print black boxes for non-Latin text."""
from __future__ import annotations
import io
import pymupdf

BASE_CSS = """
* { font-family: sans-serif; }
body { font-size: 10pt; color: #10262B; line-height: 1.45; }
h1 { font-size: 17pt; margin: 0 0 4pt 0; }
h2 { font-size: 12.5pt; margin: 12pt 0 4pt 0; color: #0F6B5F; }
h3 { font-size: 10.5pt; margin: 8pt 0 2pt 0; }
p { margin: 2pt 0 5pt 0; }
.muted { color: #5E6E72; font-size: 8.5pt; }
.box { border: 1px solid #DCE3E1; background: #F4F7F6; padding: 6pt; margin: 4pt 0; }
.quote { border-left: 2px solid #0F6B5F; padding-left: 6pt; color: #26393D; font-size: 9pt; }
.tag { color: #0F6B5F; font-weight: bold; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0; }
td, th { border: 1px solid #DCE3E1; padding: 3pt 4pt; font-size: 9pt; vertical-align: top; text-align: left; }
th { background: #EEF2F0; }
.num { font-size: 14pt; font-weight: bold; }
"""


def html_to_pdf(body_html: str, css: str = "") -> bytes:
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=BASE_CSS + css)
    page = pymupdf.paper_rect("a4")
    where = page + (42, 42, -42, -46)
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()
