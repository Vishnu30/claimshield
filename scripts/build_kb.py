"""Build the ClaimShield knowledge base from public sources — run ONCE (re-runs are incremental and free).

  python scripts/build_kb.py                 fetch all sources, then extract everything
  python scripts/build_kb.py --fetch-only    download PDFs only (no model)
  python scripts/build_kb.py --dry-run       what would be extracted + token estimate
  python scripts/build_kb.py --only policies irdai

Fallback chain at every step:
  fetch    direct URL → DuckDuckGo → Bing → you drop the PDF into data/raw/<category>/ by hand
  extract  local Llama (Ollama, free) → Muse-open on Baseten → Muse-open on Fireworks → deterministic parser
  verify   every rule keeps an exact quote + page; quotes that are not in the PDF are discarded
Output: kb/policies/*.json, kb/irdai_rules.json, kb/irdai_non_payables.json, kb/ombudsman_awards.json, kb/tariffs.json, kb/manifest.json
"""
from __future__ import annotations
import argparse, csv, json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from rapidfuzz import fuzz
import config
from config import PATHS
from core import llm, kb, sources
from core.ingest import load
from core.privacy import Vault
from core.extract import extract_policy
from core.doc_identify import identify, match_source
from core import cache as _cache

RAW = PATHS["raw"]
ROLE = "kb"
RULE_SCHEMA = {"type": "object", "properties": {"rules": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "topic": {"type": "string", "enum": ["ped", "waiting_period", "proportionate", "non_payable", "cashless", "rejection", "docs", "intimation", "escalation", "moratorium", "portability", "settlement", "other"]},
    "rule": {"type": "string"}, "section": {"type": ["string", "null"]}, "page": {"type": "integer"}, "quote": {"type": "string"},
    "overrides_policy": {"type": "boolean"}, "keywords": {"type": "array", "items": {"type": "string"}}}, "required": ["rule", "page", "quote"]}}}}
NP_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "item": {"type": "string"}, "list": {"type": "string", "enum": ["I", "II", "III", "IV"]}, "page": {"type": "integer"}}, "required": ["item", "list"]}}}}
AWARD_SCHEMA = {"type": "object", "properties": {"awards": {"type": "array", "items": {"type": "object", "properties": {
    "office": {"type": ["string", "null"]}, "year": {"type": ["integer", "null"]}, "insurer": {"type": ["string", "null"]},
    "issue_type": {"type": "string", "enum": ["ped", "waiting_period", "proportionate", "non_payable", "room_rent", "sublimit", "missing_doc", "intimation", "exclusion", "other"]},
    "complaint": {"type": "string"}, "insurer_defence": {"type": ["string", "null"]}, "decision": {"type": "string"}, "amount_awarded": {"type": ["number", "null"]},
    "reasoning_summary": {"type": "string"}, "page": {"type": "integer"}, "award_no": {"type": ["string", "null"]}}, "required": ["complaint", "decision", "page"]}}}}
TARIFF_SCHEMA = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object", "properties": {
    "code": {"type": ["string", "null"]}, "procedure": {"type": "string"}, "city_tier": {"type": ["string", "null"]}, "rate_inr": {"type": "number"}},
    "required": ["procedure", "rate_inr"]}}}}
WATCH = ["cataract", "hernia", "appendic", "angioplasty", "ptca", "knee replacement", "hip replacement", "hysterectomy", "cholecystectomy",
         "caesarean", "lscs", "dialysis", "tonsillectomy", "septoplasty", "turp", "lithotripsy", "cabg", "fracture"]
TREAT = {"I": "not payable", "II": "subsumed into room charges", "III": "subsumed into procedure charges", "IV": "subsumed into treatment costs"}


def log(msg): print(msg, flush=True)


FAST = False
MAX_USD = 3.0


def ask(prompt: str, body: str, schema: dict, label: str, max_tokens: int = 3000):
    if FAST:
        return None, "fast mode"
    cap_cloud(MAX_USD)
    try:
        data, res = llm.complete_json(ROLE, prompt, body, schema, label=label, max_tokens=max_tokens, reasoning=False)
        return data if isinstance(data, dict) else {}, res["provider"]
    except (llm.LLMUnavailable, ValueError) as e:
        return None, str(e)[:80]


def verified(q: str, t: str) -> bool:
    return bool(q) and fuzz.partial_ratio(" ".join(q.split())[:300], " ".join(t.split())) >= 88


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


INV_FILE = RAW / "inventory.json"
INVENTORY: dict[str, dict] = {}          # relative path -> identity (kind, insurer, product, uin, matched source)


def registry() -> list[dict]:
    return list(csv.DictReader((ROOT / "data" / "sources.csv").open(encoding="utf-8")))


def scan_inventory(verbose: bool = True) -> dict[str, dict]:
    """Identify every PDF under data/raw/ by its CONTENT (any file name, any sub-folder). Cached by file hash."""
    cached = json.loads(INV_FILE.read_text(encoding="utf-8")) if INV_FILE.exists() else {}
    by_hash = {v.get("sha"): v for v in cached.values()}
    rows = registry()
    INVENTORY.clear()
    files = sorted(f for f in RAW.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf")
    for f in files:
        data = f.read_bytes()
        sha = _cache.file_hash(data)
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if sha in by_hash:
            INVENTORY[rel] = {**by_hash[sha], "path": rel}
            continue
        try:
            doc = load(f.name, data, "policy")
        except Exception as e:  # noqa: BLE001
            INVENTORY[rel] = {"path": rel, "sha": sha, "kind": "unreadable", "error": str(e)[:120]}
            continue
        info = identify(doc)
        folder = f.relative_to(RAW).parts[0] if len(f.relative_to(RAW).parts) > 1 else None
        if info["kind"] == "unknown" and folder in ("policies", "irdai", "ombudsman", "tariffs", "checklists"):
            info["kind"] = folder                                      # content unclear → trust the folder
        info["source_id"] = match_source(info, " ".join(p.text for p in doc.pages[:3]), rows)
        if info["kind"] == "policies" and not info["source_id"] and (info.get("insurer") or info.get("product")):
            info["source_id"] = add_registry_row(info, rows)            # registry grows with every new product you drop in
        f, rel = canonical_move(f, info)                                # rename once → next runs are instant
        INVENTORY[rel] = {"path": rel, "sha": sha, **{k: v for k, v in info.items() if k != "scores"}}
    INV_FILE.write_text(json.dumps(INVENTORY, indent=1, ensure_ascii=False), encoding="utf-8")
    if verbose and INVENTORY:
        log(f"\n== INVENTORY ({len(INVENTORY)} PDFs, identified by content)")
        for rel, v in INVENTORY.items():
            who = " · ".join(x for x in (v.get("insurer"), v.get("product"), v.get("uin")) if x)
            log(f"  {v['kind']:11s} {Path(rel).name[:42]:42s} {who[:70]}" + (f"  ↔ {v['source_id']}" if v.get("source_id") else ""))
    return INVENTORY


CATS = ("policies", "irdai", "ombudsman", "tariffs", "checklists")


def canonical_move(f: Path, info: dict) -> tuple[Path, str]:
    """data/raw/<kind>/<source_id or insurer_product>.pdf — so the file is recognised by name next time."""
    kind = info.get("kind")
    if kind not in CATS:
        return f, str(f.relative_to(ROOT)).replace("\\", "/")
    name = info.get("source_id") or slug(info.get("insurer"), (info.get("product") or "")[:40]) or slug(info.get("title") or "")[:50] or slug(f.stem)
    if kind == "ombudsman" and re.search(r"_\d{4}_\d{2}$", f.stem):
        name = f.stem                                                   # keep monthly award names
    target = RAW / kind / f"{name}.pdf"
    if target.resolve() != f.resolve():
        n = 2
        while target.exists():
            target = RAW / kind / f"{name}_{n}.pdf"; n += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            f.rename(target)
            log(f"  ↪ renamed {f.relative_to(RAW)} → {target.relative_to(RAW)}")
            f = target
        except OSError as e:
            log(f"  (could not rename {f.name}: {e})")
    return f, str(f.relative_to(ROOT)).replace("\\", "/")


def add_registry_row(info: dict, rows: list[dict]) -> str:
    sid = "pol_" + slug(info.get("insurer"), (info.get("product") or "")[:40])
    if any(r["id"] == sid for r in rows):
        return sid
    kw = next((w for w in re.findall(r"[a-z]{5,}", (info.get("product") or "").lower()) if w not in ("health", "insurance", "policy", "family", "individual")), "")
    row = {"id": sid, "category": "policies", "mode": "search", "urls": "", "query": f"{info.get('insurer') or ''} {info.get('product') or ''} policy wording pdf".strip(),
           "keywords": kw, "domains": sources.INSURER_DOMAINS.get(info.get("insurer") or "", "")}
    rows.append(row)
    save_registry(rows)
    log(f"  + registry: {sid}  ({row['query']})")
    return sid


def save_registry(rows: list[dict]):
    cols = ["id", "category", "mode", "urls", "query", "keywords", "domains"]
    with (ROOT / "data" / "sources.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def pdfs(cat):
    if not INVENTORY:
        scan_inventory(verbose=False)
    return [ROOT / rel for rel, v in INVENTORY.items() if v.get("kind") == cat]


def slug(*parts) -> str:
    s = "_".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


# ------------------------------------------------------------------ fetch
def fetch(only, retry: bool = False):
    rows = registry()
    if only:
        rows = [r for r in rows if r["category"] in only or r["id"] in only]
    have = {v["source_id"]: Path(k).name for k, v in scan_inventory(verbose=False).items() if v.get("source_id")}
    lock = sources.load_lock()
    status = sources.load_status()
    log(f"\n== FETCH ({len(rows)} sources) — PDFs go to data/raw/<category>/")
    failed = []
    for r in rows:
        print(f"  [{r['category']}] {r['id']} … ", end="", flush=True)
        if r["id"] in have and r["mode"] != "probe":
            print(f"already present (your file: {have[r['id']]})"); continue
        try:
            msg = sources.fetch(r, lock, retry, status)
        except Exception as e:  # noqa: BLE001
            msg = f"FAILED ({e})"
        print(msg); sources.save_lock(lock); sources.save_status(status)
        if msg.startswith("FAILED") or msg == "0 file(s)":
            failed.append(r)
    if failed:
        log("\n  Not downloaded automatically — google these, save the PDF into the folder shown, re-run:")
        for r in failed:
            log(f"    data/raw/{r['category']}/   ←  {r['query'] or r['id']}")


# ------------------------------------------------------------------ extract
def cap_cloud(max_usd: float):
    """Once cloud spend reaches the cap, extraction continues on local Ollama (or the parser) only."""
    if llm.stats().cost >= max_usd and config.ROUTES["kb"] != ["ollama"]:
        config.ROUTES["kb"], config.ROUTES["kb_vision"] = ["ollama"], ["ollama_vision"]
        log(f"  $ cloud cap ${max_usd} reached → local models / parser only from here")


def do_policies(dry, fast=False, max_usd=3.0):
    files = pdfs("policies")
    log(f"\n== POLICIES ({len(files)} PDFs)")
    seen_uin = set()
    for f in files:
        meta = INVENTORY.get(str(f.relative_to(ROOT)).replace("\\", "/"), {})
        pid = slug(meta.get("insurer"), (meta.get("product") or "")[:40]) if (meta.get("insurer") and meta.get("product")) else slug(f.stem)
        if meta.get("uin") in seen_uin:
            log(f"  = {f.name}: same UIN as an earlier file — skipped"); continue
        seen_uin.add(meta.get("uin"))
        out = PATHS["policies"] / f"{pid}.json"
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if not out.exists():                       # built earlier under a file-name id → rename instead of re-extracting
            for old in PATHS["policies"].glob("*.json"):
                try:
                    d = json.loads(old.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(d.get("source_file", "")).replace("\\", "/") == rel and old.stem != pid:
                    d.update(policy_id=pid, insurer=d.get("insurer") or meta.get("insurer"), product=meta.get("product") or d.get("product"), uin=d.get("uin") or meta.get("uin"))
                    out.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8"); old.unlink()
                    log(f"  ↻ {f.name}: renamed {old.stem} → {pid} (no re-extraction)")
                    break
        if out.exists():
            log(f"  ✓ {f.name} → {pid} (already built)"); continue
        try:
            doc = load(f.name, f.read_bytes(), "policy")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {f.name}: unreadable ({e})"); continue
        log(f"  • {f.name}: {len(doc.pages)} pages{' (scanned)' if doc.scanned else ''}")
        if dry:
            continue
        t0 = time.time()
        cap_cloud(max_usd)
        try:
            pol = extract_policy(doc, Vault(), "IN", role=ROLE, policy_id=pid, save=False, fast=fast,
                                 known={k: meta.get(k) for k in ("insurer", "product", "uin")})
        except Exception as e:  # noqa: BLE001
            log(f"    ✗ failed: {e}"); continue
        if pol.room_rent.kind == "none" and not pol.waiting_periods and not pol.copays and not pol.exclusions:
            log(f"    ✗ nothing readable ({'scanned — needs Ollama llama3.2-vision or cloud vision; ' if doc.scanned else ''}not saved, will retry next run)")
            continue
        pol.source_file = str(f.relative_to(ROOT)).replace("\\", "/")
        pol.insurer = pol.insurer or meta.get("insurer")
        pol.product = meta.get("product") or pol.product or f.stem.replace("pol_", "").replace("_", " ").title()
        pol.uin = pol.uin or meta.get("uin")
        kb.save_policy(pol)
        log(f"    → kb/policies/{pid}.json  room={pol.room_rent.kind}:{pol.room_rent.value}  waiting={len(pol.waiting_periods)} "
            f"copay={len(pol.copays)} sublimits={len(pol.sublimits)} excl={len(pol.exclusions)}  ({time.time() - t0:.0f}s)")


def verify_policies():
    """Re-check every saved policy's quotes against its PDF; unverifiable quotes are removed (values kept). Free, no model calls."""
    import pymupdf
    fixed = 0
    for pid in kb.policy_library():
        p = kb.load_policy(pid)
        src = ROOT / p.source_file if p.source_file else None
        if not (src and src.exists()):
            continue
        text = " ".join(" ".join(pg.get_text() for pg in pymupdf.open(src)).split())
        if len(text) < 200:
            continue
        changed = False
        refs = [p.room_rent.cite, p.icu_rent.cite, p.proportionate_cite, *[c.cite for c in p.copays], *[s.cite for s in p.sublimits],
                *[w.cite for w in p.waiting_periods], *[e.cite for e in p.exclusions]]
        for ref in refs:
            if ref.quote and fuzz.partial_ratio(" ".join(ref.quote.split())[:200], text) < 88:
                ref.quote = None; changed = True
        if changed:
            kb.save_policy(p); fixed += 1
            log(f"  ⚠ {pid}: unverifiable quote(s) removed (values kept)")
    log(f"  → quote check: {fixed} polic{'y' if fixed == 1 else 'ies'} corrected")


def do_irdai(dry):
    files = pdfs("irdai")
    log(f"\n== IRDAI ({len(files)} PDFs)")
    rules = list(kb.rules("IN"))
    extracted, nps = [], []
    for f in files:
        try:
            doc = load(f.name, f.read_bytes(), "irdai")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {f.name}: {e}"); continue
        rel = sorted(doc.pages, key=lambda p: -len(re.findall(r"claim|waiting|pre-existing|moratorium|proportionate|non.?payable|cashless|repudiat|portab|grievance", p.text, re.I)))[:16]
        rel = sorted(rel, key=lambda p: p.no)
        log(f"  • {f.name}: {len(doc.pages)} pages, {len(rel)} relevant")
        if dry or doc.scanned:
            continue
        texts = {p.no: p.text for p in doc.pages}
        for ch in chunks(rel, 2):
            body = f"SOURCE: {f.stem}\n" + "\n".join(f"=== PAGE {p.no} ===\n{p.text}" for p in ch)
            data, who = ask("Extract binding obligations on health insurers/TPAs that a patient can cite in a claim dispute. "
                            "quote = one exact sentence copied verbatim; page = the page number shown.", body, RULE_SCHEMA, f"irdai:{f.name}:{ch[0].no}")
            if data is None:
                log(f"    p{ch[0].no}: skipped ({who})"); continue
            n0 = len(extracted)
            for r in data.get("rules", []) or []:
                if isinstance(r, dict) and verified(r.get("quote", ""), texts.get(r.get("page"), "")):
                    r.update(source=f.stem, seed=False, effective=None, topic=r.get("topic") or "other")
                    r["id"] = f"IN-{str(r['topic']).upper()}-{len(extracted) + 1:03d}"
                    extracted.append(r)
            log(f"    p{ch[0].no}-{ch[-1].no}: +{len(extracted) - n0} verified rule(s) via {who}")
            if re.search(r"list\s*-?\s*(i|ii|iii|iv)\b|non.?payable", body, re.I):
                d2, who2 = ask("Extract every item of the non-payable lists (List I to IV) on these pages.", body, NP_SCHEMA, f"np:{f.name}:{ch[0].no}")
                for it in (d2 or {}).get("items", []) or []:
                    if isinstance(it, dict) and it.get("list") in TREAT and it.get("item"):
                        nps.append({"item": it["item"].lower().strip(), "list": it["list"], "treatment": TREAT[it["list"]], "aliases": [],
                                    "source": f.stem, "source_page": it.get("page"), "seed": False})
    if dry:
        return
    for r in rules:
        if r.get("seed"):
            cand = [e for e in extracted if e["topic"] in (r["topic"], "other")]
            m = max(cand, key=lambda e: fuzz.token_set_ratio(e["rule"], r["rule"]), default=None)
            if m and fuzz.token_set_ratio(m["rule"], r["rule"]) >= 70:
                r.update(quote=m["quote"], page=m["page"], source=m["source"], seed=False)
    rules += extracted
    (PATHS["kb"] / "irdai_rules.json").write_text(json.dumps(rules, indent=1, ensure_ascii=False), encoding="utf-8")
    if len({n["item"] for n in nps}) > 40:
        seed = {n["item"]: n for n in kb.non_payables("IN")}
        uniq = {n["item"]: {**n, "aliases": seed.get(n["item"], {}).get("aliases", [])} for n in nps}
        (PATHS["kb"] / "irdai_non_payables.json").write_text(json.dumps(list(uniq.values()), indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"  → {len(rules)} rules ({sum(1 for r in rules if not r.get('seed'))} with verbatim quotes); non-payables: {len(nps)} extracted")


def do_ombudsman(dry, max_files=8):
    files = sorted(pdfs("ombudsman"), reverse=True)[:max_files]
    log(f"\n== OMBUDSMAN ({len(files)} PDFs)")
    awards = []
    for f in files:
        try:
            doc = load(f.name, f.read_bytes(), "ombudsman")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {f.name}: {e}"); continue
        pages = [p for p in doc.pages if re.search(r"health|mediclaim|hospital", p.text, re.I)][:12]
        log(f"  • {f.name}: {len(pages)} health page(s)")
        if dry or doc.scanned:
            continue
        for ch in chunks(pages, 2):
            body = "\n".join(f"=== PAGE {p.no} ===\n{p.text}" for p in ch)
            data, who = ask("Extract each HEALTH insurance ombudsman award on these pages. Be faithful; never invent facts.", body, AWARD_SCHEMA, f"omb:{f.name}:{ch[0].no}")
            for a in (data or {}).get("awards", []) or []:
                if isinstance(a, dict) and a.get("complaint"):
                    a.update(id=f"OMB-{len(awards) + 1:03d}", pdf=f.name, illustrative=False,
                             keywords=[a.get("issue_type", "")] + re.findall(r"[a-z]{5,}", (a.get("complaint", "") + " " + a.get("reasoning_summary", "")).lower())[:12])
                    awards.append(a)
    if not dry and awards:
        (PATHS["kb"] / "ombudsman_awards.json").write_text(json.dumps(awards, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"  → {len(awards)} awards" + ("" if awards or dry else " (illustrative seed precedents kept)"))


def do_tariffs(dry):
    files = pdfs("tariffs")
    log(f"\n== TARIFFS ({len(files)} PDFs)")
    rows = []
    for f in files:
        try:
            doc = load(f.name, f.read_bytes(), "tariff")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ {f.name}: {e}"); continue
        pages = [p for p in doc.pages if any(w in p.text.lower() for w in WATCH)][:16]
        log(f"  • {f.name}: {len(pages)} page(s) with watch-list procedures")
        if dry or doc.scanned:
            continue
        scheme = "CGHS" if "cghs" in f.name.lower() else "PMJAY" if re.search(r"pm.?jay|hbp|nha", f.name, re.I) else "OTHER"
        for ch in chunks(pages, 2):
            body = "\n".join(f"=== PAGE {p.no} ===\n{p.text}" for p in ch)
            data, _ = ask(f"Extract tariff rows only for procedures matching: {', '.join(WATCH)}. rate_inr = rate as printed.", body, TARIFF_SCHEMA, f"tar:{f.name}:{ch[0].no}")
            for r in (data or {}).get("rows", []) or []:
                if isinstance(r, dict) and r.get("procedure") and r.get("rate_inr"):
                    r.update(scheme=scheme, source=f.name, aliases=[]); rows.append(r)
    if not dry and rows:
        (PATHS["kb"] / "tariffs.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"  → {len(rows)} tariff rows")


def manifest():
    kb.rules.cache_clear(); kb.non_payables.cache_clear(); kb.precedents.cache_clear(); kb.tariffs.cache_clear()
    m = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "policies": [{"id": k, "name": v} for k, v in kb.policy_library().items()],
         "regulator_rules": len(kb.rules("IN")), "verbatim_rules": sum(1 for r in kb.rules("IN") if not r.get("seed")),
         "non_payables": len(kb.non_payables("IN")), "precedents": len([p for p in kb.precedents("IN") if not p.get("illustrative")]),
         "tariff_rows": len(kb.tariffs()), "raw_pdfs": {c: len(pdfs(c)) for c in ("irdai", "policies", "ombudsman", "tariffs", "checklists")}}
    (PATHS["kb"] / "manifest.json").write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"\n== KB: {len(m['policies'])} policies · {m['regulator_rules']} rules ({m['verbatim_rules']} verbatim) · "
        f"{m['non_payables']} non-payables · {m['precedents']} awards · {m['tariff_rows']} tariffs  → kb/manifest.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="policies irdai ombudsman tariffs (or source ids for fetch)")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--discover", action="store_true", help="search every Indian health insurer for policy wordings not yet in data/sources.csv")
    ap.add_argument("--fast", action="store_true", help="deterministic parser only (no model calls) — seconds per policy")
    ap.add_argument("--retry", action="store_true", help="retry sources that were not found in the last 30 days")
    ap.add_argument("--max-usd", type=float, default=3.0, help="cloud spend cap for this run; local Ollama is always free (default 3)")
    a = ap.parse_args()
    FAST, MAX_USD = a.fast, a.max_usd
    llm.set_stats(llm.RunStats())
    chain = [p for p in config.ROUTES["kb"] if llm.provider_ready(p)]
    vchain = [p for p in config.ROUTES["kb_vision"] if llm.provider_ready(p)]
    log(f"Text extraction:  {chain or ['deterministic parser only']}   ({llm.ollama_status('ollama')[1]})")
    log(f"Scan extraction:  {vchain or ['skipped (no vision model)']}   ({llm.ollama_status('ollama_vision')[1]})")
    log(f"Cloud cap: ${a.max_usd}{'  · FAST mode: parser only' if a.fast else ''}")
    scan_inventory()
    if a.discover and not a.dry_run:
        rows = registry()
        known_urls = {u for r in rows for u in (r.get("urls") or "").split("|") if u} | {v.get("url", "") for v in sources.load_lock().values()}
        log("\n== DISCOVER (searching every insurer for new health policy wordings)")
        new = sources.discover(known_urls, {r["id"] for r in rows})
        if new:
            save_registry(rows + new)
            for r in new:
                log(f"  + {r['id']}  {r['urls'][:90]}")
        log(f"  → {len(new)} new source(s) added to data/sources.csv")
    if not a.extract_only and not a.dry_run:
        fetch(a.only, a.retry)
        scan_inventory()
    if not a.fetch_only:
        cats = [c for c in (a.only or []) if c in CATS] or ["policies", "irdai", "ombudsman", "tariffs"]
        if "policies" in cats:
            do_policies(a.dry_run, a.fast, a.max_usd)
            if not a.dry_run:
                verify_policies()
        if "irdai" in cats: do_irdai(a.dry_run)
        if "ombudsman" in cats: do_ombudsman(a.dry_run)
        if "tariffs" in cats: do_tariffs(a.dry_run)
        if not a.dry_run:
            manifest()
    s = llm.stats()
    log(f"\nDone: {s.calls} model call(s) ({s.cached} cached) · cloud spend ${s.cost:.3f}")
