"""Recorded live runs of the verified samples. Anyone who clones the repo sees real Muse agent output — no keys, $0.
Created with `python scripts/demo.py snapshot` (one live pass per sample); stored in data/snapshots/<id>.json."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from config import PATHS, ROOT
from core.schemas import ClaimReport, Bill, Discharge, Rejection, ClaimContext

DIR = ROOT / "data" / "snapshots"            # verified samples — committed to git, everyone gets them
LOCAL_DIR = ROOT / "logs" / "snapshots"      # challenge samples / your own re-runs — local only (gitignored)


def path(sid: str):
    for d in (DIR, LOCAL_DIR):
        if (d / f"{sid}.json").exists():
            return d / f"{sid}.json"
    return DIR / f"{sid}.json"


def exists(sid: str) -> bool:
    return (DIR / f"{sid}.json").exists() or (LOCAL_DIR / f"{sid}.json").exists()


def update_letters(sid: str, kind: str, letter: dict) -> None:
    if exists(sid):
        p = path(sid)
        d = json.loads(p.read_text(encoding="utf-8"))
        d.setdefault("letters", {})[kind] = letter
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def update_report(sid: str, report) -> None:
    """Store extra languages added after recording, so switching back is instant."""
    if exists(sid):
        p = path(sid)
        d = json.loads(p.read_text(encoding="utf-8"))
        d["report"] = json.loads(report.model_dump_json())
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def save(sid: str, res, letters: dict, profile: str, local: bool = False) -> None:
    target = LOCAL_DIR if local else DIR
    target.mkdir(parents=True, exist_ok=True)
    b = res.bundle
    data = {"scenario": sid, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "profile": profile,
            "report": json.loads(res.report.model_dump_json()), "letters": letters,
            "bundle": {"policy_id": b.policy.policy_id, "bill": b.bill.model_dump(), "discharge": b.discharge.model_dump(),
                       "rejection": b.rejection.model_dump() if b.rejection else None, "ctx": b.ctx.model_dump()}}
    (target / f"{sid}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load(sid: str):
    """→ (RunResult, letters, meta) rebuilt exactly as recorded, or None."""
    if not exists(sid):
        return None
    from core import kb
    from core.ingest import load as load_doc
    from core.privacy import Vault
    from core.tools.checks import Bundle
    from core.pipeline import RunResult, scenario_files
    d = json.loads(path(sid).read_text(encoding="utf-8"))
    bd = d["bundle"]
    pol = kb.load_policy(bd["policy_id"])
    b = Bundle(policy=pol, bill=Bill.model_validate(bd["bill"]), ctx=ClaimContext.model_validate(bd["ctx"]),
               discharge=Discharge.model_validate(bd["discharge"]), rejection=Rejection.model_validate(bd["rejection"]) if bd.get("rejection") else None,
               clause_index=kb.ClauseIndex(pol.clauses))
    docs = {}
    fl = scenario_files(PATHS["scenarios"] / sid)
    for name, data in fl["bills"]:
        docs[name] = load_doc(name, data, "bill")
    if pol.source_file and (ROOT / pol.source_file).exists():
        docs["policy"] = load_doc((ROOT / pol.source_file).name, (ROOT / pol.source_file).read_bytes(), "policy")
    rep = ClaimReport.model_validate(d["report"])
    meta = {"recorded_at": d["recorded_at"], "profile": d.get("profile"), "providers": rep.provider_calls, "scenario": sid,
            "local": path(sid).parent == LOCAL_DIR}
    return RunResult(report=rep, bundle=b, docs=docs, vault=Vault(), seconds=0.0), d.get("letters", {}), meta
