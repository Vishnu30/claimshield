"""Spend ledger: every LLM call is logged to logs/usage.jsonl; hard stop at per-provider cap."""
from __future__ import annotations
import json, time, threading
from config import PATHS, PROVIDERS

USAGE = PATHS["logs"] / "usage.jsonl"
_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def _group(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("budget_group", provider)


def cost_of(provider: str, tin: int, tout: int) -> float:
    p = PROVIDERS[provider]
    return tin / 1e6 * p["price_in"] + tout / 1e6 * p["price_out"]


def spent() -> dict[str, float]:
    out: dict[str, float] = {}
    if USAGE.exists():
        for line in USAGE.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("cached"):
                continue
            g = _group(r.get("provider", "?"))
            out[g] = out.get(g, 0.0) + float(r.get("usd", 0))
    return out


def caps() -> dict[str, float]:
    return {k: v["budget"] for k, v in PROVIDERS.items() if "budget" in v and not v.get("local")}


def check(provider: str) -> None:
    g = _group(provider)
    cap = PROVIDERS.get(g, {}).get("budget")
    if cap is not None and spent().get(g, 0) >= cap:
        raise BudgetExceeded(f"{g} budget ${cap} reached")


def log(provider: str, model: str, role: str, tin: int, tout: int, latency: float, cached: bool, ok: bool, note: str = "") -> float:
    usd = 0.0 if cached else cost_of(provider, tin, tout)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": provider, "model": model, "role": role,
           "tin": tin, "tout": tout, "latency_s": round(latency, 2), "usd": round(usd, 6),
           "cached": cached, "ok": ok, "note": note[:200]}
    with _lock, USAGE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return usd
