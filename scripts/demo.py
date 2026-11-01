"""Demo + credit tools.

  python scripts/demo.py warm       run the 4 demo cases once live (PROFILE=prod) → every later take replays from cache at $0
  python scripts/demo.py credits    spend per provider vs. budget, average cost per call and runs left
  python scripts/demo.py snapshot [ids…|--all]   record live runs of the verified samples into data/snapshots/ (commit them:
                                    anyone who clones the repo then sees real Muse output with no keys)
Record the video with LLM_MODE=replay (cache only; never spends).
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config
from config import PATHS, PROVIDERS

DEMO = [("S01_cataract_pre_discharge", "hi", "hospital"), ("S11_scanned_cataract_gujarati", "gu", None),
        ("S20_short_settlement_pd_telugu", "te", "appeal"), ("S08_ped_rejection_appeal", "en", "appeal")]


def warm():
    from core.pipeline import run_scenario
    from core import appeal, agents, llm
    config.USE_SIDECAR = False
    total = 0.0
    for sid, lang, letter in DEMO:
        r = run_scenario(sid, engine="full", user_lang=lang, use_files=True)
        cost = r.report.cost_usd
        if letter:
            llm.set_stats(llm.RunStats()); appeal.draft(r.bundle, r.report, letter); cost += llm.stats().cost
        total += cost
        print(f"{sid}: {r.report.llm_calls} calls, ${cost:.3f}, payout {r.report.payout_before:,.0f} → {r.report.payout_after_fixes:,.0f}")
    r = run_scenario(DEMO[0][0], engine="rules")
    llm.set_stats(llm.RunStats()); agents.ask_policy(r.bundle, "Is cataract covered in the first year?", "hi"); total += llm.stats().cost
    print(f"warm-up ${total:.3f} — now record with LLM_MODE=replay")


def credits():
    usage = PATHS["logs"] / "usage.jsonl"
    rows = [json.loads(l) for l in usage.read_text(encoding="utf-8").splitlines()] if usage.exists() else []
    spent, per_role, cached = defaultdict(float), defaultdict(list), 0
    for r in rows:
        if r.get("cached"):
            cached += 1; continue
        if not r.get("ok"):
            continue
        g = PROVIDERS.get(r["provider"], {}).get("budget_group", r["provider"])
        spent[g] += r["usd"]; per_role[(g, r["role"])].append(r["usd"])
    for g in ("muse", "baseten", "fireworks"):
        cap = PROVIDERS[g]["budget"]
        print(f"{g:10s} ${spent.get(g, 0):7.2f} / ${cap:.0f}   left ${cap - spent.get(g, 0):.2f}")
    if per_role:
        print("\navg $/call by role")
        for (g, role), v in sorted(per_role.items()):
            print(f"  {g:10s} {role:9s} ${sum(v) / len(v):.4f} × {len(v)}")
    print(f"\n{cached} cache hits (free).")


FEATURED = ["S01_cataract_pre_discharge", "S11_scanned_cataract_gujarati", "S13_multi_bill_duplicate_bengali", "S14_pd_no_differential_kannada",
            "S20_short_settlement_pd_telugu", "S08_ped_rejection_appeal", "S21_portability_ignored_appeal_hinglish", "S22_late_intimation_rejection_appeal"]


def snapshot(args):
    from core.pipeline import run_scenario
    from core import appeal, llm, snapshots
    if not llm.available():
        print("No AI provider configured — add keys to .env first (snapshots must be real Muse runs)."); return
    scen = sorted(p.name for p in PATHS["scenarios"].iterdir() if (p / "truth.json").exists() and json.loads((p / "truth.json").read_text(encoding="utf-8")).get("tier", "verified") == "verified")
    ids = scen if "--all" in args else ([a for a in args if not a.startswith("--")] or FEATURED)
    config.USE_SIDECAR = False
    total = 0.0
    for sid in ids:
        t = json.loads((PATHS["scenarios"] / sid / "truth.json").read_text(encoding="utf-8"))
        r = run_scenario(sid, engine="full", user_lang=t["ctx"].get("user_lang", "en"), use_files=True)
        letters = {}
        for kind, status in (("appeal", "contestable"), ("hospital", "fixable")):
            if any(f.status == status for f in r.report.findings):
                llm.set_stats(llm.RunStats()); letters[kind] = appeal.draft(r.bundle, r.report, kind); r.report.cost_usd += llm.stats().cost
        snapshots.save(sid, r, letters, config.PROFILE)
        total += r.report.cost_usd
        hosts = ", ".join(f"{p} {d['calls']}" for p, d in r.report.provider_calls.items())
        print(f"✓ {sid}: {r.report.engine}, {hosts}, ${r.report.cost_usd:.3f}")
    print(f"\n{len(ids)} snapshot(s) in data/snapshots/ · ${total:.2f} · commit them with git")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "credits"
    if cmd == "snapshot":
        snapshot(sys.argv[2:])
    else:
        {"warm": warm, "credits": credits}.get(cmd, credits)()
