"""Learned overlays — small, reviewable rule/prompt additions written by the Muse self-improvement loop
(scripts/muse_studies.py) after a study finds a failure and a validation run proves the fix helps.

kb/learned/prompt_notes.json    {role: ["guidance line", ...]}      appended to that role's system prompt
kb/learned/np_aliases.json      {non-payable item: ["alias", ...]}  extra spellings for the non-payable matcher
kb/learned/reason_patterns.json [{"pattern": regex, "issue_type": t}]  rejection-reason classifier, checked first
kb/learned/glossary.json        {lang: {"english term": "translation"}}  kept consistent by the localizer
Delete a file to undo everything it learned; every accepted change is listed in kb/learned/CHANGELOG.json.
"""
from __future__ import annotations
import copy, json, re
from functools import lru_cache
from config import PATHS

DIR = PATHS["kb"] / "learned"
DEFAULTS = {"prompt_notes": {}, "np_aliases": {}, "reason_patterns": [], "glossary": {}}
ROLES = {"extract", "tpa", "advocate", "jury", "critic", "localize", "appeal", "chat", "single"}
ISSUES = {"ped", "waiting_period", "non_payable", "proportionate", "missing_doc", "intimation", "exclusion", "copay", "room_rent", "other"}


@lru_cache(maxsize=None)
def _read(name: str):
    p = DIR / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else copy.deepcopy(DEFAULTS[name])
    except Exception:  # noqa: BLE001  a broken overlay must never break the app
        return copy.deepcopy(DEFAULTS[name])


def get(name: str):
    return copy.deepcopy(_read(name))


def save(name: str, data) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    reload()


def reload() -> None:
    _read.cache_clear()


def notes(role: str) -> list[str]:
    return [n for n in _read("prompt_notes").get(role, []) if isinstance(n, str)][:12]


def with_notes(role: str, system: str) -> str:
    ns = notes(role)
    return system if not ns else system + "\n\nLessons learned from evaluation (follow them):\n" + "\n".join(f"- {n}" for n in ns)


def np_aliases() -> dict[str, list[str]]:
    return _read("np_aliases")


def reason_type(text: str) -> str | None:
    for p in _read("reason_patterns"):
        try:
            if p.get("issue_type") in ISSUES and re.search(p["pattern"], text, re.I):
                return p["issue_type"]
        except (re.error, KeyError, TypeError):
            continue
    return None


def glossary(lang: str) -> dict[str, str]:
    g = _read("glossary").get(lang, {})
    return g if isinstance(g, dict) else {}
