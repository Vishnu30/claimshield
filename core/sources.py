"""Source collector library (used by scripts/build_kb.py). No API keys, $0.

Fetch regulator circulars, policy wordings, ombudsman awards and tariffs into data/raw/<category>/ — no API keys, $0.

python scripts/fetch_sources.py                 # everything in data/sources.csv
python scripts/fetch_sources.py --only irdai policies
python scripts/fetch_sources.py --status        # what is already downloaded
Per source: direct URL(s) → search (DuckDuckGo HTML, then Bing) filtered by keywords/domains → download.
Files found via search on a non-official domain are flagged VERIFY in data/sources_lock.csv.
Anything that fails can be downloaded by hand and dropped into data/raw/<category>/ — extract_kb.py reads whatever is there.
"""
from __future__ import annotations
import argparse, base64, csv, hashlib, re, sys, time, urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
import requests
import urllib3
urllib3.disable_warnings()

RAW = ROOT / "data" / "raw"
REG = ROOT / "data" / "sources.csv"
LOCK = ROOT / "data" / "sources_lock.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-IN,en;q=0.9", "Accept": "text/html,application/pdf,*/*;q=0.8"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
S = requests.Session(); S.headers.update(UA)


def get(url: str, timeout: int = 40, stream: bool = False):
    for verify in (True, False):                     # many .gov.in certs are broken
        for attempt in range(2):
            try:
                r = S.get(url, timeout=timeout, verify=verify, stream=stream, allow_redirects=True)
                if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(3); continue
                return r
            except requests.exceptions.SSLError:
                break
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(2); continue
                return None
    return None


def is_pdf(b: bytes) -> bool:
    return b[:4] == b"%PDF"


def download(url: str, dest: Path) -> tuple[bool, str]:
    r = get(url, timeout=90, stream=True)
    if r is None or r.status_code != 200:
        return False, f"HTTP {getattr(r, 'status_code', 'error')}"
    data = r.content
    if not is_pdf(data):
        return False, "not a PDF"
    if len(data) < 2048:
        return False, "too small"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part"); tmp.write_bytes(data); tmp.replace(dest)
    return True, hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- search engines (no keys)
def ddg(query: str) -> list[tuple[str, str]]:
    r = get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    return parse_ddg(r.text) if r is not None and r.ok else []


def parse_ddg(html: str) -> list[tuple[str, str]]:
    out = []
    for href, text in re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        m = re.search(r"uddg=([^&]+)", href)
        url = urllib.parse.unquote(m.group(1)) if m else href
        out.append((url, re.sub("<.*?>", "", text)))
    return out


def bing(query: str) -> list[tuple[str, str]]:
    r = get("https://www.bing.com/search?q=" + urllib.parse.quote(query))
    return parse_bing(r.text) if r is not None and r.ok else []


def parse_bing(html: str) -> list[tuple[str, str]]:
    out = []
    for href, text in re.findall(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href = href.replace("&amp;", "&")
        m = re.search(r"[?&]u=a1([^&]+)", href)
        if m:
            try:
                enc = m.group(1); href = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode("utf-8", "ignore")
            except Exception:
                pass
        out.append((href, re.sub("<.*?>", "", text)))
    return out


def score(url: str, text: str, kws: list[str], domains: list[str]) -> int:
    u, t = url.lower(), text.lower()
    s = 0
    if u.split("?")[0].endswith(".pdf"):
        s += 5
    if any(d and d in u for d in domains):
        s += 6
    for k in kws:
        if any(alt and (alt in u or alt in t) for alt in k.split("|")):
            s += 3
    if any(bad in u for bad in ("scribd", "dokumen", "pdfcoffee", "slideshare", "youtube")):
        s -= 10
    return s


def links_on_page(url: str) -> list[tuple[str, str]]:
    r = get(url)
    if r is None or not r.ok or "html" not in r.headers.get("content-type", ""):
        return []
    return [(urllib.parse.urljoin(r.url, h), re.sub("<.*?>", "", t)) for h, t in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S)]


def google(query: str) -> list[tuple[str, str]]:
    r = get("https://www.google.com/search?hl=en&num=10&q=" + urllib.parse.quote(query))
    return parse_google(r.text) if r is not None and r.ok else []


def parse_google(html: str) -> list[tuple[str, str]]:
    out = []
    for href, text in re.findall(r'<a[^>]+href="(/url\?q=[^"]+|https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        if href.startswith("/url?q="):
            href = urllib.parse.unquote(href[7:].split("&")[0])
        if "google." in href or href.startswith("/"):
            continue
        out.append((href.replace("&amp;", "&"), re.sub("<.*?>", "", text)))
    return out


ENGINES = (ddg, bing, google)


def queries(row: dict) -> list[str]:
    q = row["query"]
    base = re.sub(r"\bpdf\b", "", q, flags=re.I).strip()
    doms = [d for d in row.get("domains", "").split("|") if d]
    qs = [q, f"{base} filetype:pdf"]
    if doms:
        qs.append(f"{base} site:{doms[0]}")
    if row.get("category") == "policies":
        qs += [f"{base} prospectus pdf", f"{base} customer information sheet pdf"]
    return list(dict.fromkeys(qs))


def via_search(row: dict, budget_s: float = 75):
    """Yield (url, trust) candidates: every engine × query variant, best-scored first, within a time budget."""
    t0 = time.time()
    kws = [row["keywords"]] if row.get("keywords") else []
    domains = [d for d in row.get("domains", "").split("|") if d]
    seen: set[str] = set()
    for q in queries(row):
        cands: list[tuple[int, str, str]] = []
        for engine in ENGINES:
            if time.time() - t0 > budget_s:
                return
            try:
                for url, text in engine(q)[:10]:
                    if url not in seen:
                        seen.add(url); cands.append((score(url, text, kws, domains), url, text))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.8)
        cands.sort(key=lambda x: -x[0])
        for sc, url, text in cands[:5]:
            if sc < 5:
                break
            if url.lower().split("?")[0].endswith(".pdf"):
                yield url, ("official" if any(d in url.lower() for d in domains) else "verify")
            else:
                for u2, t2 in sorted(links_on_page(url), key=lambda x: -score(x[0], x[1], kws, domains))[:3]:
                    if u2.lower().split("?")[0].endswith(".pdf") and score(u2, t2, kws, domains) >= 8:
                        yield u2, ("official" if any(d in u2.lower() for d in domains) else "verify")


def probe_months(pattern: str, back: int = 60):
    d = date.today().replace(day=1)
    for _ in range(back):
        url = (pattern.replace("{Mon}", MONTHS[d.month - 1][:3]).replace("{Month}", MONTHS[d.month - 1]).replace("{YYYY}", str(d.year)))
        yield url, f"{d.year}_{d.month:02d}"
        d = date(d.year - (d.month == 1), 12 if d.month == 1 else d.month - 1, 1)


def load_lock() -> dict:
    if not LOCK.exists():
        return {}
    with LOCK.open(encoding="utf-8") as f:
        return {r["file"]: r for r in csv.DictReader(f)}


def save_lock(lock: dict):
    cols = ["id", "file", "sha256", "bytes", "utc", "method", "trust", "url"]
    with LOCK.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in lock.values():
            w.writerow({c: r.get(c, "") for c in cols})


STATUS = ROOT / "data" / "sources_status.json"
RETRY_DAYS = 30


def load_status() -> dict:
    import json
    return json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}


def save_status(st: dict):
    import json
    STATUS.write_text(json.dumps(st, indent=1), encoding="utf-8")


def recently_failed(st: dict, sid: str) -> bool:
    e = st.get(sid)
    if not e or e.get("status") != "not_found":
        return False
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(e["at"])).days < RETRY_DAYS
    except Exception:
        return False


def _record(lock, sid, dest, sha, method, trust, url):
    lock[str(dest.relative_to(ROOT)).replace("\\", "/")] = {"id": sid, "file": str(dest.relative_to(ROOT)).replace("\\", "/"), "sha256": sha,
                                                         "bytes": dest.stat().st_size, "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                         "method": method, "trust": trust, "url": url}


def fetch(row: dict, lock: dict, force: bool, status: dict | None = None) -> str:
    cat, sid = row["category"], row["id"]
    status = status if status is not None else {}
    if not force and recently_failed(status, sid):
        return f"skipped (not found on {status[sid]['at'][:10]}; retries after {RETRY_DAYS} days or with --retry)"
    if row["mode"] == "probe":
        got, misses = 0, 0
        for url, tag in probe_months(row["urls"]):
            dest = RAW / cat / f"{sid}_{tag}.pdf"
            if dest.exists() and not force:
                got += 1
            else:
                ok, info = download(url, dest)
                if ok:
                    got += 1; misses = 0; _record(lock, sid, dest, info, "probe", "official", url)
                else:
                    misses += 1
            if got >= 8 or misses >= 14:
                break
        return f"{got} file(s)"
    dest = RAW / cat / f"{sid}.pdf"
    if dest.exists() and not force:
        return "already present"
    tried = 0
    for url in [u.strip() for u in (row.get("urls") or "").split("|") if u.strip()]:
        tried += 1
        ok, info = download(url, dest)
        if ok:
            _record(lock, sid, dest, info, "direct", "official", url); status.pop(sid, None)
            return f"OK (direct) {url[:90]}"
    for url, trust in via_search(row):
        tried += 1
        ok, info = download(url, dest)
        if ok:
            _record(lock, sid, dest, info, "search", trust, url); status.pop(sid, None)
            return f"OK ({trust}) {url[:90]}"
        if tried >= 8:
            break
    status[sid] = {"status": "not_found", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tried": tried}
    return f"FAILED after {tried} candidate(s) — drop the PDF into data/raw/{cat}/ (any name) or wait for the monthly retry"


# ---------------------------------------------------------------- discovery: grow the registry with new products
INSURER_DOMAINS = {
    "Star Health": "starhealth.in", "HDFC ERGO": "hdfcergo.com", "Niva Bupa": "nivabupa.com", "Care Health": "careinsurance.com",
    "ICICI Lombard": "icicilombard.com", "Bajaj Allianz": "bajajallianz.com", "Aditya Birla Health": "adityabirlacapital.com", "Tata AIG": "tataaig.com",
    "ManipalCigna": "manipalcigna.com", "SBI General": "sbigeneral.in", "New India Assurance": "newindia.co.in", "United India": "uiic.co.in",
    "Oriental Insurance": "orientalinsurance.org.in", "National Insurance": "nationalinsurance.nic.co.in", "Go Digit": "godigit.com", "Acko": "acko.com",
    "Reliance General": "reliancegeneral.co.in", "Future Generali": "futuregenerali.in", "IFFCO Tokio": "iffcotokio.co.in", "Royal Sundaram": "royalsundaram.in",
    "Chola MS": "cholainsurance.com", "Universal Sompo": "universalsompo.com", "Liberty General": "libertyinsurance.in", "Zurich Kotak": "zurichkotak.com",
    "Magma": "magmahdi.com", "Raheja QBE": "rahejaqbe.com", "Navi General": "naviinsurance.com", "Galaxy Health": "galaxyhealth.com",
    "Shriram General": "shriramgi.com", "Zuno": "hizuno.com",
}


def discover(existing_urls: set[str], existing_ids: set[str], insurers: list[str] | None = None, per_insurer: int = 4, budget_s: float = 40) -> list[dict]:
    """Search each insurer for health policy-wording PDFs not yet in the registry → new data/sources.csv rows."""
    new = []
    for name, dom in INSURER_DOMAINS.items():
        if insurers and name not in insurers:
            continue
        t0, found = time.time(), 0
        for q in (f"{name} health insurance policy wording pdf", f"site:{dom} health policy wording pdf", f"{name} health insurance prospectus pdf"):
            for engine in ENGINES:
                if time.time() - t0 > budget_s or found >= per_insurer:
                    break
                try:
                    hits = engine(q)[:10]
                except Exception:  # noqa: BLE001
                    hits = []
                for url, text in hits:
                    u = url.lower().split("?")[0]
                    if not u.endswith(".pdf") or url in existing_urls or not re.search(r"wording|policy|prospectus|\bcis\b|brochure|terms", u + " " + text.lower()):
                        continue
                    if dom.split(".")[0] not in u and name.split()[0].lower() not in u:
                        continue
                    if re.search(r"motor|travel|home|fire|marine|two.?wheeler|car|life|pet|cyber|shop", u):
                        continue
                    stem = re.sub(r"[^a-z0-9]+", "_", Path(urllib.parse.unquote(u)).stem)[:40].strip("_")
                    sid = f"auto_{re.sub(r'[^a-z0-9]+', '_', name.lower())}_{stem}"
                    if sid in existing_ids:
                        continue
                    existing_ids.add(sid); existing_urls.add(url); found += 1
                    new.append({"id": sid, "category": "policies", "mode": "search", "urls": url,
                                "query": f"{name} {re.sub(r'[_-]+', ' ', stem)} policy wording pdf", "keywords": "", "domains": dom})
                time.sleep(0.8)
    return new
