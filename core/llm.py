"""Provider-agnostic Muse client: role routing, fallback ladder, caching, budget, tool-calling loop.

Ladder per provider:  json_schema -> json_object -> plain(+repair)   x   reasoning on -> off
Failover: retry once on 429/5xx/timeout, then next provider in ROUTES[role].
"""
from __future__ import annotations
import json, re, time, contextvars
from typing import Any, Callable, Optional
import config
from config import PROVIDERS, ROUTES
from core import budget, cache


class LLMUnavailable(RuntimeError):
    pass


# ---- run-scoped stats (cost, calls, trace) ----------------------------------
class RunStats:
    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self.cost = 0.0
        self.calls = 0
        self.cached = 0
        self.trace: list[dict] = []
        self.on_event = on_event
        self.by_provider: dict[str, dict] = {}     # provider -> {calls, cached, usd, roles}

    def flush(self) -> None:
        for ev in self.__dict__.pop("_pending", []):
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception:
                    pass

    def count(self, role: str, res: dict) -> None:
        p = res.get("provider", "?")
        d = self.by_provider.setdefault(p, {"calls": 0, "cached": 0, "usd": 0.0, "tokens": 0, "roles": []})
        d["calls"] += 1
        d["cached"] += int(bool(res.get("cached")))
        d["usd"] += float(res.get("usd") or 0) if not res.get("cached") else 0.0
        d["tokens"] += int(res.get("tin") or 0) + int(res.get("tout") or 0)
        r = role.split(":")[0]
        if r not in d["roles"]:
            d["roles"].append(r)
        self.emit({"stage": role, "kind": "call", "provider": p, "model": res.get("model"), "cached": bool(res.get("cached")),
                   "tokens": int(res.get("tin") or 0) + int(res.get("tout") or 0), "usd": float(res.get("usd") or 0)})

    def emit(self, ev: dict) -> None:
        import threading
        ev.setdefault("t", time.strftime("%H:%M:%S"))
        if threading.current_thread() is not threading.main_thread() and getattr(self, "_main_only", True):
            self.trace.append(ev)                                     # worker thread: record, UI callback later via flush()
            self.__dict__.setdefault("_pending", []).append(ev)
            return
        self.trace.append(ev)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass


_stats: contextvars.ContextVar[Optional[RunStats]] = contextvars.ContextVar("stats", default=None)


def set_stats(s: RunStats) -> None:
    _stats.set(s)


def stats() -> RunStats:
    s = _stats.get()
    if s is None:
        s = RunStats()
        _stats.set(s)
    return s


_clients: dict[str, Any] = {}


def _client(provider: str):
    if provider not in _clients:
        from openai import OpenAI
        p = PROVIDERS[provider]
        _clients[provider] = OpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=config.REQUEST_TIMEOUT, max_retries=0)
    return _clients[provider]


DOWN: dict[str, str] = {}   # provider -> reason (auth / model-not-found / unreachable); skipped for the rest of the session
_CONN_FAILS: dict[str, int] = {}


_RESOLVED = config.PATHS["logs"] / "resolved_models.json"
_TRIED: set[str] = set()


def _bad_model(m: str) -> bool:
    return not m or m.startswith("http") or "/inference/" in m or " " in m


def resolve_model(provider: str) -> str | None:
    """Model id missing or pasted as a URL → ask the provider's GET /models once and pick the Muse model. Cached on disk."""
    p = PROVIDERS.get(provider, {})
    if not (p.get("base_url") and p.get("api_key")) or provider in _TRIED:
        return None
    _TRIED.add(provider)
    try:
        saved = json.loads(_RESOLVED.read_text(encoding="utf-8")) if _RESOLVED.exists() else {}
    except Exception:
        saved = {}
    if saved.get(provider):
        p["model"] = saved[provider]; return p["model"]
    try:
        import requests
        r = requests.get(p["base_url"].rstrip("/") + "/models", headers={"Authorization": f"Bearer {p['api_key']}"}, timeout=20)
        ids = [m.get("id", "") for m in r.json().get("data", [])] if r.ok else []
    except Exception:
        return None
    muse = [i for i in ids if "muse" in i.lower()]
    pick = next((i for i in muse if "1026" in i), None) or next((i for i in muse if "open" in i.lower()), None) or (muse[0] if muse else None)
    if pick:
        p["model"] = pick
        saved[provider] = pick
        _RESOLVED.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    return pick


_OLLAMA: dict[str, bool] = {}


def ollama_status(provider: str = "ollama") -> tuple[bool, str]:
    """(ready, message) for the local Ollama server + model. 1.5 s probe, cached per process."""
    p = PROVIDERS[provider]
    root = p["base_url"].rstrip("/").removesuffix("/v1")
    try:
        import requests
        r = requests.get(root + "/api/tags", timeout=1.5)
        names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return False, f"Ollama not running at {root} (start the Ollama app, or `ollama serve`)"
    want = p["model"]
    if not any(n == want or n.split(":")[0] == want.split(":")[0] and (":" not in want or n == want) for n in names):
        return False, f"Ollama running but model '{want}' not pulled (run: ollama pull {want})"
    return True, f"Ollama ready: {want}"


def provider_ready(provider: str) -> bool:
    p = PROVIDERS.get(provider, {})
    if provider in DOWN or not (p.get("base_url") and p.get("api_key")):
        return False
    if p.get("local"):
        if provider not in _OLLAMA:
            _OLLAMA[provider] = ollama_status(provider)[0]
        return _OLLAMA[provider]
    if _bad_model(p.get("model") or ""):
        if not resolve_model(provider):
            return False
    return True


def _is_fatal(e: Exception) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(x in s for x in ("401", "403", "authentication", "permission", "invalid api key", "model not found",
                                "does not exist", "notfounderror", "404"))


def available() -> bool:
    if config.LLM_MODE == "off":
        return False
    return any(provider_ready(p) for p in PROVIDERS) or config.LLM_MODE == "replay"


# ---- JSON helpers -----------------------------------------------------------
def _balanced_from(t: str, start: int):
    open_c = t[start]; close_c = "}" if open_c == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            esc = (c == "\\" and not esc)
            if c == '"' and not esc:
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    return None


def parse_json(text: str) -> Any:
    if text is None or not str(text).strip():
        raise ValueError("empty")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip(), flags=re.S).strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    first = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
    if first == -1:
        raise ValueError("no JSON found")
    whole = _balanced_from(t, first)
    if whole:
        try:
            return json.loads(whole)
        except Exception:
            pass
    else:
        fixed = _close_truncated(t)          # outermost object was cut off by max_tokens
        if fixed is not None:
            return fixed
    i = first
    while i != -1:                            # last resort: first parseable inner object
        seg = _balanced_from(t, i)
        if seg:
            try:
                return json.loads(seg)
            except Exception:
                pass
        nxt = [j for j in (t.find("{", i + 1), t.find("[", i + 1)) if j != -1]
        i = min(nxt) if nxt else -1
    raise ValueError("no JSON found")


def _close_truncated(t: str):
    """Model hit max_tokens mid-object: drop the dangling tail and close open brackets."""
    start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
    if start == -1:
        return None
    s = t[start:]
    for cut in range(len(s), max(0, len(s) - 4000), -1):
        chunk = s[:cut].rstrip().rstrip(",")
        stack, in_str, esc = [], False, False
        for c in chunk:
            if in_str:
                esc = (c == "\\" and not esc)
                if c == '"' and not esc:
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                stack.append("}" if c == "{" else "]")
            elif c in "}]" and stack:
                stack.pop()
        if in_str or not stack:
            continue
        if chunk[-1] in ":":
            continue
        try:
            return json.loads(chunk + "".join(reversed(stack)))
        except Exception:
            continue
    return None


def _is_retryable(e: Exception) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(x in s for x in ("429", "rate", "timeout", "timed out", "502", "503", "504", "500", "connection", "overloaded"))


def _is_param_error(e: Exception) -> bool:
    s = str(e).lower()
    return any(x in s for x in ("400", "422", "response_format", "json_schema", "unsupported", "invalid", "not supported", "extra", "unknown", "tool"))


# ---- single completion ------------------------------------------------------
def complete(role: str, messages: list[dict], schema: Optional[dict] = None, tools: Optional[list[dict]] = None,
             temperature: float = 0.1, max_tokens: int = 4000, reasoning: bool = True, label: str = "",
             providers: Optional[list[str]] = None) -> dict:
    """Returns {content, json, tool_calls, reasoning, provider, model, cached, usd}."""
    mode = config.LLM_MODE
    if mode == "off":
        raise LLMUnavailable("LLM_MODE=off")
    chain = [p for p in (providers or ROUTES.get(role, ["baseten"])) if p in PROVIDERS]
    errors = []
    for provider in chain:
        if _bad_model(PROVIDERS[provider].get("model") or "") and mode != "replay":
            provider_ready(provider)
        model = PROVIDERS[provider]["model"]
        k = cache.key(provider, model, messages, schema, tools, temperature)
        if config.CACHE_READ or mode == "replay":
            hit = cache.get(k)
            if hit is not None:
                budget.log(provider, model, role, hit.get("tin", 0), hit.get("tout", 0), 0, True, True, label)
                s = stats(); s.cached += 1; s.calls += 1
                hit["cached"] = True
                s.count(role, hit)
                return hit
        if mode == "replay" or not provider_ready(provider):
            errors.append(f"{provider}: not configured" if mode != "replay" else f"{provider}: cache miss")
            continue
        try:
            budget.check(provider)
        except budget.BudgetExceeded as e:
            errors.append(str(e)); continue
        res = _call_with_ladder(provider, model, role, messages, schema, tools, temperature, max_tokens, reasoning, label, errors)
        if res is not None:
            cache.put(k, res)
            res["cached"] = False
            stats().count(role, res)
            return res
    raise LLMUnavailable("; ".join(errors) or "no provider")


MAX_HTTP_PER_PROVIDER = 3          # hard cap per complete() call per provider → no runaway loops / spend


def _call_with_ladder(provider, model, role, messages, schema, tools, temperature, max_tokens, reasoning, label, errors):
    """Bounded fallback ladder: at most MAX_HTTP_PER_PROVIDER requests; one longer retry if the answer was cut off."""
    rf_ladder: list[Optional[dict]] = [None]
    if schema and not tools:
        rf_ladder = [{"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": False}},
                     {"type": "json_object"}, None]
    rs = bool(reasoning and config.REASONING_PARAMS)
    client = _client(provider)
    http_left, rung, retried_len, retried_net = MAX_HTTP_PER_PROVIDER, 0, False, False
    while http_left > 0 and rung < len(rf_ladder):
        rf = rf_ladder[rung]
        kw: dict[str, Any] = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        if rf:
            kw["response_format"] = rf
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = "auto"
        if rs:
            kw["extra_body"] = dict(config.REASONING_PARAMS)
        t0 = time.time()
        http_left -= 1
        try:
            r = client.chat.completions.create(**kw)
        except Exception as e:  # noqa: BLE001
            budget.log(provider, model, role, 0, 0, time.time() - t0, False, False, f"{label} {e}")
            if _is_fatal(e):
                DOWN[provider] = str(e)[:160]
                errors.append(f"{provider}: disabled for this session ({str(e)[:120]})")
                stats().emit({"stage": "router", "kind": "fallback", "text": f"{provider} disabled: {str(e)[:120]}"})
                return None
            if "connection" in str(e).lower() or "timed out" in str(e).lower():
                _CONN_FAILS[provider] = _CONN_FAILS.get(provider, 0) + 1
                if _CONN_FAILS[provider] >= 3:
                    DOWN[provider] = f"unreachable ({str(e)[:80]})"
                    errors.append(f"{provider}: unreachable, disabled for this session"); return None
            if rs and _is_param_error(e):
                rs = False; continue                                  # drop reasoning params, same rung
            if _is_param_error(e):
                rung += 1; continue                                   # step down the ladder
            if _is_retryable(e) and not retried_net:
                retried_net = True; time.sleep(2); continue
            errors.append(f"{provider}: {str(e)[:160]}")
            return None
        msg = r.choices[0].message
        finish = getattr(r.choices[0], "finish_reason", None)
        u = getattr(r, "usage", None)
        tin = getattr(u, "prompt_tokens", 0) or 0
        tout = getattr(u, "completion_tokens", 0) or 0
        _CONN_FAILS.pop(provider, None)
        usd = budget.log(provider, model, role, tin, tout, time.time() - t0, False, True, label)
        s = stats(); s.cost += usd; s.calls += 1
        content = msg.content or ""
        reasoning_txt = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
        if not content.strip() and reasoning_txt and schema:
            content = str(reasoning_txt)                              # some models answer only in the reasoning channel
        tcs = [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments} for tc in (msg.tool_calls or [])]
        out = {"content": content, "tool_calls": tcs, "reasoning": str(reasoning_txt)[:4000], "truncated": finish == "length",
               "provider": provider, "model": model, "tin": tin, "tout": tout, "usd": usd, "json": None}
        if not schema or tcs:
            return out
        try:
            out["json"] = parse_json(content)                         # repairs answers cut off by max_tokens
        except Exception:
            out["json"] = None
        if finish == "length" and not retried_len and http_left > 0 and max_tokens < 8000:
            if out["json"] is None or role in ("extract", "kb", "kb_vision"):   # lists must be complete for extraction
                retried_len = True
                max_tokens = min(8000, max_tokens * 2)
                stats().emit({"stage": role, "kind": "info", "text": f"answer cut off on {provider}; one retry with max_tokens={max_tokens}"})
                continue
        if out["json"] is None and finish != "length" and rf is not None:
            rung += 1; continue                                       # unparseable → lower rung
        return out
    errors.append(f"{provider}: no usable answer within {MAX_HTTP_PER_PROVIDER} requests" + (" (tools unsupported?)" if tools else ""))
    return None


def complete_json(role: str, system: str, user: Any, schema: dict, label: str = "", **kw) -> tuple[Any, dict]:
    content = user if isinstance(user, list) else (user if isinstance(user, str) else json.dumps(user, ensure_ascii=False))
    messages = [{"role": "system", "content": system + "\nReturn ONLY valid JSON matching the schema. No prose, no markdown."},
                {"role": "user", "content": content}]
    res = complete(role, messages, schema=schema, label=label, **kw)
    data = res.get("json")
    if data is None:
        for txt in (res.get("content", ""), res.get("reasoning", "")):
            try:
                data = parse_json(txt); break
            except ValueError:
                continue
    if data is None:
        raw = (res.get("content") or res.get("reasoning") or "")[:6000]
        stats().emit({"stage": role, "kind": "info", "text": f"non-JSON answer from {res.get('provider')} → repair pass"})
        fix = complete(role, [{"role": "system", "content": "Convert the text into ONE valid JSON object matching the schema. Output JSON only."},
                              {"role": "user", "content": f"SCHEMA:\n{json.dumps(schema)[:3000]}\n\nTEXT:\n{raw or '(empty answer)'}"}],
                       schema=schema, label=label + "#repair", max_tokens=kw.get("max_tokens", 4000), reasoning=False)
        data = fix.get("json")
        if data is None:
            data = parse_json(fix.get("content", ""))   # raises ValueError → caller falls back
    return data, res


# ---- tool-calling agent loop -----------------------------------------------
def run_tool_agent(role: str, system: str, user: str, tool_specs: list[dict], tool_fns: dict[str, Callable],
                   final_schema: dict, max_turns: int | None = None, label: str = "", deadline_s: float | None = None) -> tuple[Any, list[dict]]:
    """Native OpenAI tool-calling; falls back to JSON action protocol if the provider rejects `tools`.
    Bounded by max_turns AND a wall-clock deadline — then it asks for the final answer."""
    max_turns = max_turns or config.MAX_AGENT_TURNS
    t_start = time.time()
    deadline_s = deadline_s or config.AGENT_DEADLINE_S
    steps: list[dict] = []
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    native = True
    for turn in range(max_turns):
        if time.time() - t_start > deadline_s:
            stats().emit({"stage": role, "kind": "info", "text": f"time budget {deadline_s:.0f}s reached after {turn} turn(s) → final answer"})
            break
        try:
            if native:
                res = complete(role, messages, tools=tool_specs, label=f"{label}#t{turn}")
            else:
                res = complete(role, messages, schema={"type": "object"}, label=f"{label}#p{turn}")
        except LLMUnavailable as e:
            if native and "tool" in str(e).lower():
                native = False
                messages = _protocol_messages(system, user, tool_specs)
                continue
            raise
        if res.get("reasoning"):
            stats().emit({"stage": role, "kind": "reasoning", "text": res["reasoning"][:1500], "provider": res["provider"]})
        calls = res.get("tool_calls") or []
        if native and calls:
            messages.append({"role": "assistant", "content": res.get("content") or None,
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]})
            for c in calls:
                result = _exec_tool(c["name"], c["arguments"], tool_fns)
                steps.append({"tool": c["name"], "args": c["arguments"], "result": result})
                stats().emit({"stage": role, "kind": "tool", "tool": c["name"], "args": c["arguments"], "result": _short(result)})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
            continue
        if not native:
            try:
                act = parse_json(res.get("content", ""))
            except Exception:
                act = {}
            if isinstance(act, dict) and act.get("action") == "tool":
                result = _exec_tool(act.get("name", ""), json.dumps(act.get("args", {})), tool_fns)
                steps.append({"tool": act.get("name"), "args": act.get("args"), "result": result})
                stats().emit({"stage": role, "kind": "tool", "tool": act.get("name"), "args": act.get("args"), "result": _short(result)})
                messages.append({"role": "assistant", "content": res.get("content", "")})
                messages.append({"role": "user", "content": "TOOL_RESULT " + json.dumps(result, ensure_ascii=False, default=str)[:6000]})
                continue
            final = act.get("result", act) if isinstance(act, dict) else act
            return final, steps
        # native, no tool calls -> final answer
        try:
            return parse_json(res.get("content", "")), steps
        except Exception:
            fixed, _ = complete_json(role, "Convert the following answer into JSON matching the schema.",
                                     (res.get("content") or "")[:8000] + "\nSCHEMA:\n" + json.dumps(final_schema),
                                     final_schema, label=f"{label}#repair")
            return fixed, steps
    # turn budget exhausted: force a final answer without tools
    messages.append({"role": "user", "content": "Tool budget exhausted. Give your FINAL answer now as JSON only."})
    res = complete(role, messages, schema=final_schema, label=f"{label}#final")
    return (res.get("json") or parse_json(res.get("content", ""))), steps


def _protocol_messages(system: str, user: str, tool_specs: list[dict]) -> list[dict]:
    catalog = [{"name": t["function"]["name"], "description": t["function"]["description"],
                "parameters": t["function"]["parameters"]} for t in tool_specs]
    sys = (system + "\n\nYou can call tools. Reply with exactly one JSON object per turn: "
           '{"action":"tool","name":<tool>,"args":{...}} to call a tool, or {"action":"final","result":{...}} when done.\n'
           "TOOLS:\n" + json.dumps(catalog))
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def _exec_tool(name: str, arguments: str, fns: dict[str, Callable]) -> Any:
    fn = fns.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
        return fn(**args)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _short(x: Any) -> str:
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) < 400 else s[:400] + "…"
