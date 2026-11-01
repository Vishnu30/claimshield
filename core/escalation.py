"""Escalation ladder with dates + .ics calendar export (jurisdiction-pack driven)."""
from __future__ import annotations
from datetime import date, timedelta, datetime, timezone
from core import kb
from core.tools.checks import parse_date


def timeline(jurisdiction: str, start: str | None) -> list[dict]:
    pack = kb.jurisdiction(jurisdiction)
    d0 = parse_date(start) or date.today()
    out, cursor = [], d0
    for i, s in enumerate(pack.get("escalation", [])):
        e = dict(s)
        if i == 0:
            e["file_by"] = str(d0 + timedelta(days=s.get("file_within_days") or 7))
            cursor = d0 + timedelta(days=7)
        else:
            wait = s.get("wait_days_before") or out[-1].get("respond_days") or 14
            cursor = cursor + timedelta(days=wait)
            e["earliest"] = str(cursor)
            if s.get("file_within_days"):
                e["deadline"] = str(d0 + timedelta(days=s["file_within_days"]))
        if s.get("respond_days"):
            e["expect_reply_by"] = str(cursor + timedelta(days=s["respond_days"]))
        out.append(e)
    return out


def to_ics(steps: list[dict], claim_ref: str = "claim") -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ClaimShield//EN"]
    for n, s in enumerate(steps):
        d = s.get("file_by") or s.get("earliest") or s.get("deadline")
        if not d:
            continue
        ds = d.replace("-", "")
        lines += ["BEGIN:VEVENT", f"UID:{claim_ref}-{n}@claimshield", f"DTSTAMP:{now}", f"DTSTART;VALUE=DATE:{ds}",
                  f"SUMMARY:ClaimShield: {s['step']}", f"DESCRIPTION:{s.get('how','')}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
