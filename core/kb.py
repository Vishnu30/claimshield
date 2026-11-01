"""Knowledge base: jurisdiction packs, regulator rules, non-payables, precedents, policy library, BM25 clause search."""
from __future__ import annotations
import json, math, re
from functools import lru_cache
from collections import Counter
from config import PATHS
from core.schemas import PolicyRules, Clause

TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("the a an of and or to in for on by with is are be as at this that any from shall will which per such not no".split())


def tok(s: str) -> list[str]:
    return [t for t in TOKEN.findall((s or "").lower()) if t not in STOP and len(t) > 1]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.docs = [tok(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.docs)) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, q: str) -> list[float]:
        qt = tok(q)
        out = []
        for d, tf in zip(self.docs, self.tf):
            s = 0.0
            for t in qt:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out

    def top(self, q: str, k: int = 5) -> list[tuple[int, float]]:
        sc = self.scores(q)
        return [(i, s) for i, s in sorted(enumerate(sc), key=lambda x: -x[1])[:k] if s > 0]


def _load(name: str | None, default):
    """Built knowledge base (kb/<name>, written by build_kb) wins; otherwise the shipped seed (kb/seed/<name>)."""
    if not name:
        return default
    p = PATHS["kb"] / name
    if not p.exists():
        p = PATHS["kb"] / "seed" / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


@lru_cache(maxsize=8)
def jurisdiction(code: str) -> dict:
    p = PATHS["kb"] / "jurisdictions" / f"{code}.json"
    if not p.exists():
        p = PATHS["kb"] / "jurisdictions" / "GENERIC.json"
    return json.loads(p.read_text(encoding="utf-8"))


def jurisdictions() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((PATHS["kb"] / "jurisdictions").glob("*.json"))]


@lru_cache(maxsize=8)
def rules(code: str) -> list[dict]:
    return _load(jurisdiction(code).get("rules_file"), [])


def rule(code: str, rule_id: str) -> dict | None:
    return next((r for r in rules(code) if r["id"] == rule_id), None)


@lru_cache(maxsize=8)
def non_payables(code: str) -> list[dict]:
    return _load(jurisdiction(code).get("non_payables_file"), [])


@lru_cache(maxsize=8)
def precedents(code: str) -> list[dict]:
    return _load(jurisdiction(code).get("precedents_file"), [])


@lru_cache(maxsize=1)
def checklists() -> dict:
    return _load("doc_checklists.json", {})


@lru_cache(maxsize=1)
def tariffs() -> list[dict]:
    return _load("tariffs.json", [])


def policy_library() -> dict[str, str]:
    out = {}
    for p in sorted(PATHS["policies"].glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out[p.stem] = f"{d.get('insurer') or ''} {d.get('product') or p.stem}".strip()
        except Exception:
            continue
    return out


def load_policy(policy_id: str) -> PolicyRules:
    return PolicyRules.model_validate_json((PATHS["policies"] / f"{policy_id}.json").read_text(encoding="utf-8"))


def save_policy(p: PolicyRules) -> None:
    (PATHS["policies"] / f"{p.policy_id}.json").write_text(p.model_dump_json(indent=1), encoding="utf-8")


class ClauseIndex:
    def __init__(self, clauses: list[Clause]):
        self.clauses = clauses
        self.bm = BM25([f"{c.title} {c.text}" for c in clauses]) if clauses else None

    def search(self, q: str, k: int = 3) -> list[Clause]:
        if not self.bm:
            return []
        return [self.clauses[i] for i, _ in self.bm.top(q, k)]

    def get(self, clause_id: str) -> Clause | None:
        cid = (clause_id or "").strip().lower()
        return next((c for c in self.clauses if c.id.lower() == cid), None)


def search_rules(code: str, q: str, k: int = 3) -> list[dict]:
    rs = rules(code)
    if not rs:
        return []
    bm = BM25([f"{r['topic']} {r['rule']} {' '.join(r.get('keywords', []))}" for r in rs])
    return [rs[i] for i, _ in bm.top(q, k)]


def search_precedents(code: str, q: str, k: int = 3) -> list[dict]:
    ps = precedents(code)
    if not ps:
        return []
    bm = BM25([f"{p.get('issue_type','')} {p.get('complaint','')} {p.get('reasoning_summary','')} {' '.join(p.get('keywords', []))}" for p in ps])
    return [ps[i] for i, _ in bm.top(q, k)]


def split_clauses(pages: list[str], prefix: str = "C") -> list[Clause]:
    """Heuristic clause splitter for policy wordings: numbered headings / Excl codes / blank-line paragraphs."""
    head = re.compile(r"^\s*((?:\d+(?:\.\d+){0,3})|(?:Excl\s?\d+)|(?:[A-Z]\.\d+)|(?:\([a-z]\)))[\s.)-]+(.{0,120})$")
    out: list[Clause] = []
    for pno, text in enumerate(pages, start=1):
        cur_id, cur_title, buf = None, "", []

        def flush():
            body = " ".join(" ".join(buf).split())
            if len(body) > 40:
                cid = cur_id or f"{prefix}{pno}.{len(out) + 1}"
                out.append(Clause(id=cid, title=cur_title[:120], page=pno, text=body[:6000]))

        for line in text.splitlines():
            m = head.match(line)
            if m and len(line) < 160:
                flush()
                cur_id, cur_title, buf = m.group(1).replace(" ", ""), m.group(2).strip(), [line]
            elif not line.strip() and len(" ".join(buf)) > 900:
                flush(); cur_id, cur_title, buf = None, "", []
            else:
                buf.append(line)
        flush()
    # de-duplicate ids
    seen: Counter = Counter()
    for c in out:
        seen[c.id] += 1
        if seen[c.id] > 1:
            c.id = f"{c.id}#{seen[c.id]}"
    return out
