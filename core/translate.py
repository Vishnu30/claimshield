"""Batch translation on Muse (Fireworks → Baseten by default): small parallel batches so long Indic-script output never truncates.
Used for findings, summaries, action plans, letters and the UI itself. Every batch is cached (same text = $0)."""
from __future__ import annotations
import contextvars, json
from concurrent.futures import ThreadPoolExecutor
from core import llm, prompts

SCHEMA = {"type": "object", "properties": {"t": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["t"]}
SYSTEM = ("Translate every value into {lang_name} for an Indian patient's family: simple, warm, everyday words. "
          "Keep numbers, ₹ amounts, dates, clause ids (3.1, Excl05) and rule ids (IN-PD-NODIFF) exactly as they are. "
          "For Hinglish use Roman-script Hindi-English. Return JSON {{\"t\": {{<same key>: <translation>}}}} with EVERY key.")


def _batches(items: dict[str, str], max_chars: int = 900, max_keys: int = 6):
    cur, size = {}, 0
    for k, v in items.items():
        if cur and (size + len(v) > max_chars or len(cur) >= max_keys):
            yield cur; cur, size = {}, 0
        cur[k] = v; size += len(v)
    if cur:
        yield cur


def translate(items: dict[str, str], lang: str, label: str = "tr") -> dict[str, str]:
    """{key: english} → {key: translated}; missing keys fall back to English. Never raises."""
    items = {k: v for k, v in items.items() if isinstance(v, str) and v.strip()}
    if lang == "en" or not items or not llm.available():
        return dict(items)
    name = prompts.LANG_NAMES.get(lang, lang)
    out: dict[str, str] = {}

    def run(batch):
        data, _ = llm.complete_json("localize", SYSTEM.format(lang_name=name), json.dumps(batch, ensure_ascii=False), SCHEMA,
                                    label=f"{label}:{lang}", reasoning=False, max_tokens=2000)
        t = data.get("t", data) if isinstance(data, dict) else {}
        return {k: str(v) for k, v in t.items() if k in batch and isinstance(v, (str, int, float)) and str(v).strip()}

    batches = list(_batches(items))
    with ThreadPoolExecutor(max_workers=min(8, len(batches))) as ex:
        futs = [ex.submit(contextvars.copy_context().run, run, b) for b in batches]
        for f in futs:
            try:
                out.update(f.result(timeout=90))
            except Exception:  # noqa: BLE001
                continue
    llm.stats().flush()
    return {k: out.get(k, v) for k, v in items.items()}
