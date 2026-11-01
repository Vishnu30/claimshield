"""Closed-loop self-improvement used by muse_studies.py.

After a study finds failures, the Muse API diagnoses them and proposes at most 3 small, general changes to the
learned overlays (kb/learned/*.json — prompt notes, non-payable aliases, rejection-reason patterns, glossary).
The change is applied, the deterministic test-suite runs offline, the failing cases and a few passing "control"
cases are re-run live. It is KEPT only if it fixes at least one failure and breaks nothing; otherwise it is reverted.
Every decision is logged to kb/learned/CHANGELOG.json and shown in the README.

Python code is never rewritten automatically: crashes are logged to docs/IMPROVEMENTS.md for a developer.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import llm, kb, learned

LOG = learned.DIR / "CHANGELOG.json"
LANGS = {"hi", "hinglish", "gu", "mr", "kn", "ta", "te", "bn", "ml", "pa", "or", "es"}
SCHEMA = {"type": "object", "properties": {
    "diagnosis": {"type": "string"},
    "changes": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["prompt_note", "np_alias", "reason_pattern", "glossary"]},
        "role": {"type": "string"}, "text": {"type": "string"}, "item": {"type": "string"}, "alias": {"type": "string"},
        "pattern": {"type": "string"}, "issue_type": {"type": "string"}, "lang": {"type": "string"},
        "term": {"type": "string"}, "translation": {"type": "string"}}, "required": ["kind"]}}},
    "required": ["diagnosis", "changes"]}
SYSTEM = """You improve ClaimShield, a patient-side health-insurance claim auditor (India, IRDAI rules).
A study found failures. Propose AT MOST 3 small, GENERAL changes that would fix them, using ONLY these levers:
- prompt_note: one guidance line (<= 30 words) appended to the system prompt of a role in {roles}
- np_alias: an extra spelling for an EXISTING non-payable item (item must be copied exactly from the list given)
- reason_pattern: a Python regex that classifies insurer rejection-reason text into one of {issues}
- glossary: a consistent translation of an English term for a language code in {langs}
Rules: never mention claim ids, patient names or specific rupee amounts; never weaken safety (money is computed by Python,
findings must stay cited); prefer the smallest change; if the failure is a test artefact or cannot be fixed with these levers,
return no changes and say why in "diagnosis". Return JSON only."""


def _log(entry: dict):
    learned.DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    data.append({"time": time.strftime("%Y-%m-%d %H:%M"), **entry})
    LOG.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def log_for_developer(study: str, items: list[str]):
    if not items:
        return
    p = ROOT / "docs" / "IMPROVEMENTS.md"
    head = "# Issues found by Muse studies that need a developer\n\nAuto-fixes only touch kb/learned/*. Crashes and code-level gaps are listed here.\n"
    old = p.read_text(encoding="utf-8") if p.exists() else head
    p.parent.mkdir(exist_ok=True)
    p.write_text(old + f"\n## {study} · {time.strftime('%Y-%m-%d %H:%M')}\n" + "\n".join(f"- {x}" for x in items) + "\n", encoding="utf-8")
    _log({"study": study, "change": f"{len(items)} item(s) logged to docs/IMPROVEMENTS.md", "fixed": "—", "regressions": "—", "accepted": "logged"})


def _sanitize(changes: list[dict], roles: set[str], kinds: set[str]) -> list[dict]:
    names = {n["item"] for n in kb.non_payables("IN")}
    ok = []
    for c in changes or []:
        k = c.get("kind")
        if k not in kinds:
            continue
        if k == "prompt_note":
            t = str(c.get("text", "")).strip()
            if c.get("role") in roles and 10 <= len(t) <= 240 and not re.search(r"\b[SC]\d{2}\b|₹\s?\d", t):
                ok.append({"kind": k, "role": c["role"], "text": t})
        elif k == "np_alias":
            al = str(c.get("alias", "")).strip().lower()
            if c.get("item") in names and 3 <= len(al) <= 60 and al not in learned.np_aliases().get(c["item"], []):
                ok.append({"kind": k, "item": c["item"], "alias": al})
        elif k == "reason_pattern":
            pat = str(c.get("pattern", ""))
            try:
                re.compile(pat)
            except re.error:
                continue
            if 3 <= len(pat) <= 120 and c.get("issue_type") in learned.ISSUES and not re.fullmatch(r"\.?\*?", pat):
                ok.append({"kind": k, "pattern": pat, "issue_type": c["issue_type"]})
        elif k == "glossary":
            if c.get("lang") in LANGS and 2 <= len(str(c.get("term", ""))) <= 40 and 1 <= len(str(c.get("translation", ""))) <= 60:
                ok.append({"kind": k, "lang": c["lang"], "term": str(c["term"]), "translation": str(c["translation"])})
    return ok[:3]


def _apply(changes: list[dict]) -> dict:
    backup = {n: learned.get(n) for n in learned.DEFAULTS}
    pn, na, rp, gl = learned.get("prompt_notes"), learned.get("np_aliases"), learned.get("reason_patterns"), learned.get("glossary")
    for c in changes:
        if c["kind"] == "prompt_note":
            pn.setdefault(c["role"], []).append(c["text"]); pn[c["role"]] = pn[c["role"]][-12:]
        elif c["kind"] == "np_alias":
            na.setdefault(c["item"], []).append(c["alias"])
        elif c["kind"] == "reason_pattern":
            rp.insert(0, {"pattern": c["pattern"], "issue_type": c["issue_type"]})
        else:
            gl.setdefault(c["lang"], {})[c["term"]] = c["translation"]
    learned.save("prompt_notes", pn); learned.save("np_aliases", na); learned.save("reason_patterns", rp); learned.save("glossary", gl)
    return backup


def _revert(backup: dict):
    for n, v in backup.items():
        learned.save(n, v)


def _offline_tests_pass() -> bool:
    env = {**os.environ, "LLM_MODE": "off"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_tools.py", "tests/test_robustness.py"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return r.returncode == 0


def describe(c: dict) -> str:
    return {"prompt_note": lambda: f"{c['role']} prompt: “{c['text']}”",
            "np_alias": lambda: f"non-payable alias: “{c['alias']}” → {c['item']}",
            "reason_pattern": lambda: f"reason pattern /{c['pattern']}/ → {c['issue_type']}",
            "glossary": lambda: f"{c['lang']} glossary: {c['term']} → {c['translation']}"}[c["kind"]]()


def improve(study: str, failures: list[dict], rerun, controls: list, roles: set[str], kinds: set[str], spend, max_cases: int = 5) -> bool:
    """failures: [{"case": <anything rerun() accepts>, "context": {...shown to Muse...}}]; returns True if a change was kept."""
    if not failures:
        _log({"study": study, "change": "no failures — nothing to learn", "fixed": "—", "regressions": "—", "accepted": "n/a"})
        return False
    if spend.over:
        return False
    print(f"\n  ↻ improve[{study}]: {len(failures)} failure(s) → asking Muse API for a fix", flush=True)
    names = sorted(n["item"] for n in kb.non_payables("IN")) if "np_alias" in kinds else []
    payload = {"study": study, "failures": [f["context"] for f in failures[:8]],
               "current_overlays": {n: learned.get(n) for n in ("prompt_notes", "np_aliases", "reason_patterns")},
               **({"non_payable_items": names} if names else {})}
    st = llm.RunStats(); llm.set_stats(st)
    try:
        out, _ = llm.complete_json("improve", SYSTEM.format(roles=sorted(roles), issues=sorted(learned.ISSUES), langs=sorted(LANGS)),
                                   json.dumps(payload, ensure_ascii=False)[:24000], SCHEMA, label=f"improve:{study}",
                                   reasoning=True, max_tokens=1500, providers=["muse"])
    except Exception as e:  # noqa: BLE001
        _log({"study": study, "change": f"Muse unavailable ({str(e)[:60]})", "fixed": "—", "regressions": "—", "accepted": "no"}); return False
    spend.add(st.by_provider.get("muse", {}).get("usd", 0.0), st.cost)
    changes = _sanitize((out or {}).get("changes", []), roles, kinds)
    diag = str((out or {}).get("diagnosis", ""))[:300]
    if not changes:
        _log({"study": study, "change": "no safe change proposed", "diagnosis": diag, "fixed": "—", "regressions": "—", "accepted": "no"})
        print(f"  ↻ improve[{study}]: no safe change — {diag[:120]}", flush=True)
        return False
    backup = _apply(changes)
    summary = " · ".join(describe(c) for c in changes)
    if any(c["kind"] in ("np_alias", "reason_pattern") for c in changes) and not _offline_tests_pass():
        _revert(backup)
        _log({"study": study, "change": summary, "diagnosis": diag, "fixed": "—", "regressions": "tests failed", "accepted": "reverted"})
        print("  ↻ improve: offline tests failed → reverted", flush=True)
        return False
    fixed = tried = reg = ctl = 0
    for f in failures[:max_cases]:
        if spend.over:
            break
        tried += 1; fixed += bool(rerun(f["case"]))
    for c in controls[:3]:
        if spend.over:
            break
        ctl += 1; reg += not bool(rerun(c))
    keep = fixed >= 1 and reg == 0
    if not keep:
        _revert(backup)
    _log({"study": study, "change": summary, "diagnosis": diag, "fixed": f"{fixed}/{tried}", "regressions": f"{reg}/{ctl}",
          "accepted": "kept" if keep else "reverted"})
    print(f"  ↻ improve[{study}]: {summary[:140]} → fixed {fixed}/{tried}, regressions {reg}/{ctl} → {'KEPT' if keep else 'reverted'}", flush=True)
    return keep


def changelog() -> list[dict]:
    return json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
