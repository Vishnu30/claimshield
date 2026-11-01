"""KB integrity: every non-seed rule has a verbatim quote + page; every policy quote is really in its source PDF."""
from pathlib import Path
import pymupdf
from rapidfuzz import fuzz
from core import kb

ROOT = Path(__file__).resolve().parents[1]


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def test_rules_have_provenance():
    for r in kb.rules("IN"):
        assert r["id"] and r["rule"] and r["source"]
        if not r.get("seed"):
            assert r.get("quote") and r.get("page"), r["id"]


def test_policy_quotes_verbatim():
    """Same rule the extractor uses: quote must fuzzy-match (≥88) the PDF text (or the clause text when the PDF isn't on this machine)."""
    bad = []
    for pid in kb.policy_library():
        p = kb.load_policy(pid)
        src = ROOT / p.source_file if p.source_file else None
        if src and src.exists():
            text = _norm(" ".join(pg.get_text() for pg in pymupdf.open(src)))
        else:
            text = _norm(" ".join(c.text for c in p.clauses))
        if len(text) < 200:
            continue                                     # scanned policy: no text layer to verify against
        refs = [p.room_rent.cite, p.icu_rent.cite, p.proportionate_cite, *[c.cite for c in p.copays], *[s.cite for s in p.sublimits],
                *[w.cite for w in p.waiting_periods]]
        for ref in refs:
            if ref.quote and fuzz.partial_ratio(_norm(ref.quote)[:200], text) < 88:
                bad.append((pid, ref.clause_id, ref.quote[:60]))
    assert not bad, bad


def test_non_payables_lists():
    assert {n["list"] for n in kb.non_payables("IN")} == {"I", "II", "III", "IV"}
