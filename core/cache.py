"""Content-addressed response cache. Same prompt+model => zero spend. Powers DEMO replay."""
from __future__ import annotations
import hashlib, json
from typing import Any
from config import PATHS


def key(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(k: str) -> Any | None:
    p = PATHS["cache"] / f"{k}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def put(k: str, value: Any) -> None:
    (PATHS["cache"] / f"{k}.json").write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
