"""Setup doctor.

  python scripts/doctor.py              validate .env, providers, routes, data, git hygiene  ($0, no network)
  python scripts/doctor.py --live       + tiny live probe of every provider: chat / JSON / tools / audio / vision  (~$0.003)
  python scripts/doctor.py --models fireworks     list the Muse model ids your key can use
"""
from __future__ import annotations
import importlib, os, re, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ERR, WARN = [], []


def ok(msg, fix=""): print(f"  ✅ {msg}")
def warn(msg, fix=""): WARN.append(msg); print(f"  ⚠️  {msg}" + (f"\n      fix: {fix}" if fix else ""))
def err(msg, fix=""): ERR.append(msg); print(f"  ❌ {msg}" + (f"\n      fix: {fix}" if fix else ""))


import json, time, base64
from openai import OpenAI

TOOL = [{"type": "function", "function": {"name": "add", "description": "add two numbers",
         "parameters": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}}}]


def probe(name, p):
    import config
    if not (p["base_url"] and p["api_key"]):
        print(f"⚪ {name:10s} not configured"); return
    from core import llm as _llm
    if _llm._bad_model(p.get("model") or ""):
        got = _llm.resolve_model(name)
        print(f"🔎 {name}: model id invalid → " + (f"auto-resolved to {got}" if got else "no Muse model found via /models (run scripts/list_models.py)"))
        if not got:
            return
    c = OpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=60, max_retries=0)
    m = p["model"]
    def t(label, **kw):
        t0 = time.time()
        try:
            r = c.chat.completions.create(model=m, max_tokens=60, **kw)
            msg = r.choices[0].message
            out = msg.tool_calls[0].function.name if msg.tool_calls else (msg.content or "")[:50].replace("\n", " ")
            print(f"   ✅ {label:22s} {time.time() - t0:4.1f}s  {out}")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"   ❌ {label:22s} {str(e)[:110]}")
            return False
    print(f"🔌 {name} · {m}")
    t("chat", messages=[{"role": "user", "content": "Reply with the word OK."}])
    t("json_schema", messages=[{"role": "user", "content": "Return {\"ok\":true}"}],
      response_format={"type": "json_schema", "json_schema": {"name": "o", "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}})
    t("json_object", messages=[{"role": "user", "content": "Return JSON {\"ok\":true}"}], response_format={"type": "json_object"})
    t("tool calling", messages=[{"role": "user", "content": "Use the tool to add 2 and 3."}], tools=TOOL)
    if config.REASONING_PARAMS:
        t("reasoning params", messages=[{"role": "user", "content": "2+2?"}], extra_body=config.REASONING_PARAMS)
    import pymupdf, base64
    d = pymupdf.open(); pg = d.new_page(width=200, height=80); pg.insert_text((10, 40), "TOTAL Rs 1,234", fontsize=14)
    url = "data:image/png;base64," + base64.b64encode(pg.get_pixmap(dpi=100).tobytes("png")).decode()
    import io, wave, math
    buf = io.BytesIO(); w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(int(8000 * math.sin(2 * math.pi * 440 * i / 16000)).to_bytes(2, "little", signed=True) for i in range(8000))); w.close()
    t("audio input", messages=[{"role": "user", "content": [{"type": "text", "text": "Describe this audio in 3 words."},
      {"type": "input_audio", "input_audio": {"data": base64.b64encode(buf.getvalue()).decode(), "format": "wav"}}]}])
    t("vision", messages=[{"role": "user", "content": [{"type": "text", "text": "What number is written?"}, {"type": "image_url", "image_url": {"url": url}}]}])




def list_models(name: str):
    import requests
    import config
    p = config.PROVIDERS[name]
    url = p["base_url"].rstrip("/") + "/models"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {p['api_key']}"}, timeout=30)
    except Exception as e:  # noqa: BLE001
        print(f"{url}: {e}"); return
    print(f"{url} → HTTP {r.status_code}")
    if r.ok:
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        muse = [i for i in ids if "muse" in i.lower()]
        print("Muse models:", *(muse or ["(none found)"]), sep="\n  ")
        print(f"\nPut one into {name.upper()}_MODEL= in .env")
    else:
        print(r.text[:300])


def main() -> int:
    print("Environment")
    v = sys.version_info
    (ok if v >= (3, 10) else err)(f"Python {v.major}.{v.minor}", "install Python 3.10–3.12")
    for mod, minv in (("streamlit", "1.46"), ("openai", "1.40"), ("pydantic", "2.6"), ("pymupdf", "1.24"), ("reportlab", "4.0"), ("rapidfuzz", "3.6"), ("dotenv", None), ("pandas", None)):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", getattr(m, "VersionBind", "?"))
            if minv and str(ver)[0].isdigit() and tuple(int(x) for x in re.findall(r"\d+", str(ver))[:2]) < tuple(int(x) for x in minv.split(".")):
                err(f"{mod} {ver} < {minv}", "pip install -r requirements.txt --upgrade")
            else:
                ok(f"{mod} {ver}")
        except Exception as e:  # noqa: BLE001
            err(f"{mod} missing ({e})", "pip install -r requirements.txt")

    print("\n.env")
    envp = ROOT / ".env"
    if not envp.exists():
        warn("no .env — the app runs in offline mode (deterministic engine, samples, digital PDFs)", "copy .env.example .env and add keys for the AI agents")
    else:
        raw = envp.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*\w+\s+=|=\s+\S", raw, re.M):
            ok("spaces around '=' detected — tolerated")
        if re.search(r"=\s*[^#\n]*\s#", raw):
            ok("inline comments detected — tolerated")
    import config
    for name, p in config.PROVIDERS.items():
        if name == "muse_cheap" or p.get("local"):
            continue
        tag = f"{name}:"
        if not p["api_key"]:
            warn(f"{tag} no API key — provider disabled", f"set {name.upper()}_API_KEY in .env"); continue
        if " " in p["api_key"]:
            err(f"{tag} API key contains spaces", "re-paste the key")
        if not p["base_url"].startswith("https://"):
            err(f"{tag} base URL must start with https:// ({p['base_url'] or 'empty'})")
        elif not p["base_url"].rstrip("/").endswith("/v1"):
            warn(f"{tag} base URL usually ends with /v1 ({p['base_url']})")
        m = p["model"]
        if not m:
            err(f"{tag} model id empty", f"set {name.upper()}_MODEL")
        elif m.startswith("http") or "/inference/" in m:
            from core import llm as _llm
            fixed = _llm.resolve_model(name)
            if fixed:
                ok(f"{tag} model id was a URL → auto-resolved to {fixed} (saved in logs/resolved_models.json; also put it in .env)"); continue
            err(f"{tag} model id is a URL ({m}) and auto-discovery failed", "python scripts/doctor.py --models fireworks  → copy the Muse id into " + f"{name.upper()}_MODEL")
        else:
            ok(f"{tag} {p['base_url']}  model={m}  key=…{p['api_key'][-3:]}")
    for name in ("baseten", "muse", "fireworks"):
        p = config.PROVIDERS[name]
        if p["price_in"] <= 0 or p["price_out"] <= 0:
            warn(f"{name}: prices not set — budget guard cannot track spend")
    (ok if config.PROFILE == "prod" else warn)(f"PROFILE={config.PROFILE}" + ("" if config.PROFILE == "prod" else " — Muse API roles temporarily on Baseten (credit saving)"),
                                             "set PROFILE=prod for the demo/judging run")
    (ok if config.LLM_MODE in ("live", "replay", "off") else err)(f"LLM_MODE={config.LLM_MODE}", "live | replay | off")

    print("\nRoutes (first ready provider per role)")
    from core import llm
    if not any(llm.provider_ready(p) for p in config.PROVIDERS if not config.PROVIDERS[p].get("local")):
        warn("no cloud provider ready → every role uses the deterministic fallback")
    for role, chain in ({} if not any(llm.provider_ready(p) for p in config.PROVIDERS if not config.PROVIDERS[p].get("local")) else config.ROUTES).items():
        ready = [p for p in chain if llm.provider_ready(p)]
        lab = config.PROVIDER_LABEL
        note = "" if not ready or ready[0] == chain[0] else f"   (primary {lab.get(chain[0], chain[0])} not ready → failover)"
        (ok if ready else warn)(f"{config.ROLE_INFO[role][0]:24s} → {lab.get(ready[0], ready[0]) if ready else 'deterministic fallback'}{note}")
    jury = [p for p in config.JURY_PROVIDERS if llm.provider_ready(p)]
    (ok if len(jury) >= 2 else warn)(f"jury panel: {jury}", "needs ≥2 ready providers for engine=full jury")

    print("\nData")
    sc = [d for d in (ROOT / "data" / "scenarios").glob("S*") if (d / "truth.json").exists()]
    (ok if len(sc) >= 24 else err)(f"{len(sc)} scenarios", "python scripts/make_scenarios.py")
    ok_, msg = __import__("core.llm", fromlist=["x"]).ollama_status()
    (ok if ok_ else warn)(msg + " — only needed to rebuild the knowledge base for free", "install Ollama, then: ollama pull llama3.1:8b")
    pols = list((ROOT / "kb" / "policies").glob("*.json"))
    (ok if pols else err)(f"{len(pols)} policies in library", "python scripts/make_scenarios.py")
    raw = sum(1 for _ in (ROOT / "data" / "raw").rglob("*.pdf"))
    (ok if raw else warn)(f"{raw} source PDFs in data/raw (optional — the shipped knowledge base works without them)", "python scripts/build_kb.py")
    try:
        t = ROOT / "logs" / ".w"; t.write_text("x"); t.unlink(); ok("logs/ writable")
    except Exception as e:  # noqa: BLE001
        err(f"logs/ not writable: {e}")

    print("\nGit hygiene")
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""
    (ok if re.search(r"^\.env$", gi, re.M) else err)(".env ignored by .gitignore")
    try:
        r = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            warn("not a git repository yet", "git init  (see README → Publish)")
        elif r.stdout.strip():
            err(".env is TRACKED by git — keys would be published", "git rm --cached .env && git commit -m 'untrack env'")
        else:
            ok(".env not tracked")
    except FileNotFoundError:
        warn("git not installed", "install Git for Windows")

    print(f"\nRESULT: {len(ERR)} error(s), {len(WARN)} warning(s)")
    return 1 if ERR else 0


if __name__ == "__main__":
    if "--models" in sys.argv:
        list_models(sys.argv[sys.argv.index("--models") + 1] if len(sys.argv) > sys.argv.index("--models") + 1 else "fireworks"); sys.exit(0)
    code = main()
    if "--live" in sys.argv:
        import config
        print("\nLive provider probes (~$0.003)")
        for k, v in config.PROVIDERS.items():
            if not v.get("local"):
                probe(k, v)
        from core import llm as _llm
        print("  ollama:", _llm.ollama_status()[1])
    sys.exit(code)
