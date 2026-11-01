"""Central settings. Everything comes from .env so providers / routes / budgets swap without code changes."""
from __future__ import annotations
import os, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def env(key: str, default: str = "") -> str:
    v = os.getenv(key)
    if v is None:
        return default
    v = re.split(r"\s+#", " " + v, maxsplit=1)[0].strip()   # tolerate inline "  # comment" in .env
    if v.startswith("#"):
        v = ""
    v = v.strip().strip('"').strip("'").strip()
    return default if v == "" else v


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, str(default)).lower() in ("1", "true", "yes", "on")


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key, str(default)).replace("$", "").replace(",", ""))
    except ValueError:
        return default


PATHS = {
    "kb": ROOT / "kb",
    "policies": ROOT / "kb" / "policies",
    "scenarios": ROOT / "data" / "scenarios",
    "raw": ROOT / "data" / "raw",
    "logs": ROOT / "logs",
    "cache": ROOT / "logs" / "cache",
    "eval": ROOT / "eval",
}
for p in PATHS.values():
    p.mkdir(parents=True, exist_ok=True)

# ---- Providers (all OpenAI-compatible) -------------------------------------
PROVIDERS = {
    "baseten": {
        "base_url": env("BASETEN_BASE_URL", "https://inference.baseten.co/v1"),
        "api_key": env("BASETEN_API_KEY"),
        "model": env("BASETEN_MODEL", "meta-models/muse-spark-open-1026"),
        "price_in": env_float("PRICE_BASETEN_IN", 0.50),    # USD per 1M tokens (set real values)
        "price_out": env_float("PRICE_BASETEN_OUT", 1.50),
        "budget": env_float("BUDGET_BASETEN_USD", 100),
    },
    "muse": {
        "base_url": env("MUSE_BASE_URL"),
        "api_key": env("MUSE_API_KEY"),
        "model": env("MUSE_MODEL", "muse-spark-1.3"),
        "price_in": env_float("PRICE_MUSE_IN", 1.00),
        "price_out": env_float("PRICE_MUSE_OUT", 4.00),
        "budget": env_float("BUDGET_MUSE_USD", 150),
    },
    "fireworks": {
        "base_url": env("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"),
        "api_key": env("FIREWORKS_API_KEY"),
        "model": env("FIREWORKS_MODEL", "accounts/fireworks/models/muse-spark-open"),
        "price_in": env_float("PRICE_FIREWORKS_IN", 0.50),
        "price_out": env_float("PRICE_FIREWORKS_OUT", 1.50),
        "budget": env_float("BUDGET_FIREWORKS_USD", 100),
    },
    # Local Meta Llama via Ollama — free, offline; used only to BUILD the knowledge base (never in the claim runtime)
    "ollama": {
        "base_url": env("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "api_key": "ollama",
        "model": env("OLLAMA_MODEL", "llama3.1:8b"),
        "price_in": 0.0, "price_out": 0.0, "budget": 1e9, "local": True,
    },
    "ollama_vision": {
        "base_url": env("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "api_key": "ollama",
        "model": env("OLLAMA_VISION_MODEL", "llama3.2-vision"),
        "price_in": 0.0, "price_out": 0.0, "budget": 1e9, "local": True,
    },
    "muse_cheap": {
        "base_url": env("MUSE_BASE_URL"),
        "api_key": env("MUSE_API_KEY"),
        "model": env("MUSE_MODEL_CHEAP", "muse-spark-1.3-contributor"),
        "price_in": env_float("PRICE_MUSE_CHEAP_IN", 0.30),
        "price_out": env_float("PRICE_MUSE_CHEAP_OUT", 1.20),
        "budget_group": "muse",   # shares the $150 Muse cap
    },
}

# ---- Role routing -----------------------------------------------------------
# One job per provider (see README → "Where Muse runs"):
#   muse      Muse API, first-party Muse Spark 1.3 (reasoning)  → the BRAIN: judgement roles, few calls per claim
#   baseten   Muse Spark open, 2M-TPM vision host                 → the EYES: high-volume document reading
#   fireworks Muse Spark open, fast serverless                    → the INDEPENDENT CHECKER + user-facing chat/translation
# Each role lists a primary then failovers. Ollama (local Llama) is used ONLY by scripts/build_kb.py.
ROLE_INFO = {
    "extract":  ("Document extractor", "Reads bills, discharge summaries, rejection letters and policy pages (vision + text). High volume, image-heavy → Baseten's 2M-TPM vision host."),
    "evidence": ("Evidence locator", "Finds the cited line on scanned bills/photos (vision grounding) → same vision host as extraction."),
    "tpa":      ("TPA Adjudicator", "Multi-turn tool-calling reasoning that predicts the insurer's cuts → first-party Muse Spark 1.3."),
    "advocate": ("Patient Advocate", "Argues each cut against policy wording and IRDAI rules with tool calls → first-party Muse Spark 1.3."),
    "jury":     ("Cross-provider jury", "Same question to all three hosts; majority vote + agreement score → one seat per provider."),
    "critic":   ("Critic", "Audits the agents' output; runs on a different host from TPA/Advocate so the check is independent → Fireworks."),
    "appeal":   ("Appeal writer", "Formal, legally grounded letters where quality matters most → first-party Muse Spark 1.3."),
    "localize": ("Localizer", "Translates explanations into 11 Indian languages; short, latency-sensitive → Fireworks."),
    "chat":     ("Ask-your-policy chat", "Interactive Q&A over retrieved clauses; low latency → Fireworks."),
    "voice":    ("Voice intake", "Transcribes the patient's voice note (input_audio) → Muse API, falling back to the open hosts."),
    "single":   ("Single-pass baseline", "Ablation only: one call, no tools, no agents → Fireworks."),
    "kb":       ("Knowledge-base builder", "One-time offline extraction of public PDFs → local Llama (free), then Baseten, then Fireworks."),
    "kb_vision": ("Knowledge-base builder (scans)", "Scanned policy PDFs → local Llama 3.2 Vision (free), then Baseten vision."),
}
_PROD = {
    "extract":  ["baseten", "fireworks", "muse"],
    "evidence": ["baseten", "fireworks", "muse"],
    "tpa":      ["muse", "baseten", "fireworks"],
    "advocate": ["muse", "baseten", "fireworks"],
    "jury":     ["muse", "baseten", "fireworks"],
    "critic":   ["fireworks", "baseten"],
    "appeal":   ["muse", "baseten", "fireworks"],
    "localize": ["fireworks", "baseten"],
    "chat":     ["fireworks", "baseten"],
    "voice":    ["muse", "baseten", "fireworks"],
    "single":   ["fireworks", "baseten"],
    "kb":       ["ollama", "baseten", "fireworks"],
    "kb_vision": ["ollama_vision", "baseten", "fireworks"],
}
# PROFILE=prod (default): the map above.  PROFILE=dev: identical, but Muse API roles move to Baseten to save Muse credits while building.
_DEV = {r: [p for p in chain if p != "muse"] + (["muse"] if "muse" in chain else []) for r, chain in _PROD.items()}
PROFILE = env("PROFILE", "prod").lower()
if PROFILE == "demo":
    PROFILE = "prod"
_DEFAULT_ROUTES = {"prod": _PROD, "dev": _DEV}
ROUTES = {r: list(c) for r, c in _DEFAULT_ROUTES.get(PROFILE, _PROD).items()}
for role in list(ROUTES):
    override = env(f"ROUTE_{role.upper()}")
    if override:
        ROUTES[role] = [x.strip() for x in override.split(",") if x.strip()]

# LLM_MODE: live (call + cache) | replay (cache only, rules fallback) | off (rules engine only, zero spend)
LLM_MODE = env("LLM_MODE", "live")
CACHE_READ = env_bool("CACHE_READ", True)
try:
    REASONING_PARAMS = json.loads(env("REASONING_PARAMS_JSON", "{}") or "{}")
    if not isinstance(REASONING_PARAMS, dict):
        REASONING_PARAMS = {}
except (json.JSONDecodeError, ValueError):
    print("[config] REASONING_PARAMS_JSON is not valid JSON — ignored (leave it empty or use {\"reasoning_effort\":\"medium\"})")
    REASONING_PARAMS = {}
def env_int(key: str, default: int) -> int:
    try:
        return int(float(env(key, str(default))))
    except ValueError:
        return default


MAX_AGENT_TURNS = env_int("MAX_AGENT_TURNS", 4)
AGENT_DEADLINE_S = env_float("AGENT_DEADLINE_S", 75)      # wall-clock budget per tool-calling agent
REQUEST_TIMEOUT = env_float("REQUEST_TIMEOUT", 60)
PAGE_DPI = env_int("PAGE_DPI", 110)
MAX_POLICY_PAGES_TO_LLM = env_int("MAX_POLICY_PAGES_TO_LLM", 14)
REDACT_PII = env_bool("REDACT_PII", True)

JURY_PROVIDERS = [p.strip() for p in env("JURY_PROVIDERS", "muse,baseten,fireworks").split(",") if p.strip()]
PROVIDER_LABEL = {"muse": "Muse API", "muse_cheap": "Muse API (contributor)", "baseten": "Baseten", "fireworks": "Fireworks", "ollama": "Ollama (local Llama)", "ollama_vision": "Ollama (local Llama Vision)"}
MAX_VISION_LOCATES = env_int("MAX_VISION_LOCATES", 6)   # Muse vision bbox calls per run (scans/photos only)
DEFAULT_JURISDICTION = env("JURISDICTION", "IN")
USE_SIDECAR = env_bool("USE_SIDECAR", False)   # demo scenarios: skip extraction spend
