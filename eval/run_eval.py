"""Eval + ablation over data/scenarios/*/expected.json.

python eval/run_eval.py --engines rules              # 0 credits
python eval/run_eval.py --engines lean full single   # uses Baseten/Muse (cached after first run)
python eval/run_eval.py --engines full --files       # also re-extract from PDFs with Muse
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from config import PATHS
from core.pipeline import run_scenario

ALIASES = {"icu_rent": "room_rent"}


def score(expected: list, got: list) -> dict:
    e = Counter(ALIASES.get(t, t) for t, _ in expected)
    g = Counter(ALIASES.get(t, t) for t, _ in got)
    tp = sum((e & g).values())
    es = Counter((ALIASES.get(t, t), s) for t, s in expected)
    gs = Counter((ALIASES.get(t, t), s) for t, s in got)
    st_ok = sum((es & gs).values())
    return {"tp": tp, "fp": sum(g.values()) - tp, "fn": sum(e.values()) - tp, "status_ok": st_ok}


def _one(sid: str, eng: str, use_files: bool, lang: str | None):
    return run_scenario(sid, engine=eng, use_files=use_files, user_lang=lang).report


def run_all(engines: list[str], use_files: bool = False, only: list[str] | None = None, max_usd: float = 999.0, tier: str = "all",
            workers: int = 4, keep_lang: bool = False) -> dict:
    """Scenarios run in parallel (workers) with a progress line each; results are saved after every engine.
    Evaluation scores detection, so explanations stay in English unless keep_lang (translation adds time, not accuracy)."""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = {}
    scen = sorted(p for p in PATHS["scenarios"].iterdir() if p.is_dir() and (p / "expected.json").exists())
    if only:
        scen = [p for p in scen if any(p.name.startswith(o) for o in only)]
    if tier != "all":
        scen = [p for p in scen if json.loads((p / "expected.json").read_text(encoding="utf-8")).get("tier", "verified") == tier]
    rp = PATHS["eval"] / "results.json"
    for eng in engines:
        rows, TP = [], Counter()
        cost = 0.0; cites = [0, 0]; money_err = 0.0
        t0, n_done = time.time(), 0
        print(f"\n== {eng}: {len(scen)} claims, {workers if eng != 'rules' else 1} in parallel", flush=True)
        with ThreadPoolExecutor(max_workers=1 if eng == "rules" else workers) as ex:
            futs = {ex.submit(_one, d.name, eng, use_files, None if keep_lang else "en"): d for d in scen}
            for fu in as_completed(futs):
                d = futs[fu]
                n_done += 1
                exp = json.loads((d / "expected.json").read_text(encoding="utf-8"))
                try:
                    r = fu.result()
                except Exception as e:  # noqa: BLE001
                    rows.append({"scenario": d.name, "error": str(e)[:120]})
                    print(f"  [{n_done:2d}/{len(scen)}] {d.name[:44]:44s} ERROR {str(e)[:60]}", flush=True)
                    continue
                got = [(f.issue_type, f.status) for f in r.findings]
                sc = score(exp["issues"], got)
                TP.update(sc)
                cost += r.cost_usd
                for f in r.findings:
                    if f.policy_cite:
                        cites[1] += 1; cites[0] += int(f.policy_cite.verified)
                if r.mode != "appeal":
                    money_err += abs(r.payout_before - exp["payout_as_is"])
                rows.append({"scenario": d.name, "expected": len(exp["issues"]), "found": len(got), "tp": sc["tp"], "fp": sc["fp"], "fn": sc["fn"],
                             "status_ok": sc["status_ok"], "payout_as_is": r.payout_before, "expected_payout": exp["payout_as_is"],
                             "after_fixes": r.payout_after_fixes, "lang": exp.get("lang", "en"), "render": exp.get("render", "pdf"),
                             "tier": exp.get("tier", "verified"), "cost_usd": r.cost_usd})
                el = time.time() - t0
                eta = el / n_done * (len(scen) - n_done)
                print(f"  [{n_done:2d}/{len(scen)}] {d.name[:44]:44s} tp={sc['tp']} fp={sc['fp']} fn={sc['fn']}  ${cost:.2f}  "
                      f"elapsed {el / 60:.1f}m  eta {eta / 60:.1f}m", flush=True)
                if cost > max_usd:
                    print(f"  budget guard: stopping {eng} at ${cost:.2f}", flush=True)
                    for f2 in futs:
                        f2.cancel()
                    break
        rows.sort(key=lambda r: r["scenario"])
        p = TP["tp"] / max(1, TP["tp"] + TP["fp"])
        rc = TP["tp"] / max(1, TP["tp"] + TP["fn"])
        out[eng] = {"precision": round(p, 3), "recall": round(rc, 3), "status_acc": round(TP["status_ok"] / max(1, TP["tp"]), 3),
                    "money_error": round(money_err, 2), "citation_validity": round(cites[0] / max(1, cites[1]), 3), "cost": round(cost, 4),
                    "per_scenario": rows}
        print(f"{eng:7s} P={p:.2f} R={rc:.2f} status={out[eng]['status_acc']:.2f} money_err={money_err:.0f} cites={out[eng]['citation_validity']:.0%} "
              f"${cost:.3f}  ({(time.time() - t0) / 60:.1f} min)", flush=True)
        prev = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
        prev[eng] = out[eng]
        rp.write_text(json.dumps(prev, indent=1), encoding="utf-8")          # saved per engine — safe to stop between engines
    print_readme_table()
    return out


NAMES = {"full": "full (TPA + Advocate + Jury + Critic)", "lean": "lean (Advocate)", "single": "single (one Muse pass, ablation)", "rules": "rules (no AI)"}


def print_readme_table():
    """Markdown table of every engine in eval/results.json — paste it into the README's Evaluation section."""
    rp = PATHS["eval"] / "results.json"
    if not rp.exists():
        return
    data = json.loads(rp.read_text(encoding="utf-8"))
    lines = ["| Engine | Precision | Recall | Status acc. | ₹ error | Citations | Cost |", "|---|---|---|---|---|---|---|"]
    for e in ("full", "lean", "single", "rules"):
        if e in data:
            r = data[e]
            n = len([x for x in r["per_scenario"] if "error" not in x])
            lines.append(f"| {NAMES[e]} | {r['precision']:.2f} | {r['recall']:.2f} | {r['status_acc']:.2f} | ₹{r['money_error']:,.0f} | "
                         f"{r['citation_validity']:.0%} | ${r.get('cost', 0):.2f} ({n} claims) |")
    print("\n--- copy into README.md → Evaluation ---\n" + "\n".join(lines) + "\n---------------------------------------")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", nargs="+", default=["rules"])
    ap.add_argument("--files", action="store_true", help="re-extract from PDFs with Muse instead of ground-truth JSON")
    ap.add_argument("--only", nargs="*", help="scenario id prefixes, e.g. S01 S11 S20")
    ap.add_argument("--max-usd", type=float, default=999.0, help="stop an engine once its eval spend exceeds this")
    ap.add_argument("--tier", default="all", choices=["all", "verified", "challenge"])
    ap.add_argument("--workers", type=int, default=4, help="claims evaluated in parallel (AI engines)")
    ap.add_argument("--table", action="store_true", help="only print the README table from saved results")
    ap.add_argument("--keep-lang", action="store_true", help="also translate results into each claim's language (slower; not scored)")
    a = ap.parse_args()
    if a.files:
        import config
        config.USE_SIDECAR = False
    if a.table:
        print_readme_table()
    else:
        run_all(a.engines, a.files, a.only, a.max_usd, a.tier, a.workers, a.keep_lang)
