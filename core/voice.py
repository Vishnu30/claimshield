"""Voice intake on Muse: audio sent as input_audio to the 'voice' route (Muse API → Baseten → Fireworks), cached by audio hash.
If no provider accepts audio, the UI asks the user to type (text in any language is handled by Muse)."""
from __future__ import annotations
import base64
from core import llm, prompts, cache


def transcribe(audio: bytes, fmt: str = "wav", lang: str | None = None) -> dict:
    k = cache.key("asr", cache.file_hash(audio), lang)
    hit = cache.get(k)
    if hit:
        return {**hit, "cached": True}
    try:
        content = [{"type": "text", "text": prompts.VOICE + (f" Expected language hint: {lang}." if lang else "")},
                   {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode(), "format": fmt}}]
        data, res = llm.complete_json("voice", "You transcribe audio.", content, {"type": "object"}, label="voice", reasoning=False)
        out = {**data, "engine": res["provider"]}
    except Exception as e:  # noqa: BLE001
        return {"text": "", "lang": None, "translation_en": None, "engine": "none", "error": str(e)[:200]}
    if out.get("text"):
        cache.put(k, out)
    return out


def translate_narrative(text: str) -> dict:
    """Typed narrative in any language -> {lang, translation_en}. Cheap route, cached."""
    if not text or not text.strip():
        return {"lang": None, "translation_en": text}
    try:
        data, _ = llm.complete_json("localize", "Detect the language (ISO 639-1 or 'hinglish') and translate to English.",
                                    text[:2000], {"type": "object", "properties": {"lang": {"type": "string"}, "translation_en": {"type": "string"}}},
                                    label="narr", reasoning=False)
        return data
    except Exception:
        return {"lang": None, "translation_en": text}
