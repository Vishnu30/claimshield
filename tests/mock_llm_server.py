"""Minimal OpenAI-compatible mock used by tests to exercise routing, ladder and tool loops without spending credits."""
from __future__ import annotations
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

CALLS: list[dict] = []
TRUNCATE = False          # simulate a host that always hits max_tokens (cut-off JSON, finish_reason=length)


def _reply(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "mock",
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}}


def respond(req: dict):
    msgs = req["messages"]
    sys = msgs[0]["content"] if isinstance(msgs[0]["content"], str) else ""
    rf = req.get("response_format", {})
    if rf.get("type") == "json_schema":
        return 400, {"error": {"message": "response_format json_schema unsupported"}}
    has_tool_result = any(m.get("role") == "tool" for m in msgs)
    if "TPA claims adjudicator" in sys:
        if req.get("tools") and not has_tool_result:
            return 200, _reply(None, [{"id": "c1", "type": "function", "function": {"name": "waiting_period_check", "arguments": json.dumps({"condition": "cataract"})}}])
        return 200, _reply(json.dumps({"deductions": [{"deduction_id": "D-PD", "agree": True, "title": "PD", "reasoning": "Deluxe room > 1% SI so clause 3.3 applies", "clause_id": "3.3", "confidence": 0.9}],
                                       "extra_risks": [{"issue_type": "other", "title": "Uncited speculation", "reasoning": "maybe", "confidence": 0.4}], "notes": "mock"}))
    if "patient's advocate" in sys:
        if req.get("tools") and not has_tool_result:
            return 200, _reply(None, [{"id": "c2", "type": "function", "function": {"name": "search_rules", "arguments": json.dumps({"query": "proportionate differential billing"})}}])
        return 200, _reply(json.dumps({"verdicts": [{"id": "F05", "status": "fixable", "fix": "Ask hospital for a no-differential-billing certificate", "argument": "IRDAI bars PD where fees do not vary", "rule_ids": ["IN-PD-NODIFF"], "precedent_ids": ["ILL-PD-01"], "confidence": 0.9}]}))
    if "three-model review panel" in sys:
        body = json.loads(msgs[1]["content"])
        return 200, _reply(json.dumps({"votes": [{"id": f["id"], "status": f.get("proposed") or f.get("proposed_status")} for f in body["findings"]]}))
    if "You locate text on a document image" in sys:
        return 200, _reply(json.dumps({"found": True, "box": [120, 240, 880, 262]}))
    if "skeptical reviewer" in sys:
        return 200, _reply(json.dumps({"reviews": [{"id": "F01", "keep": True, "concern": None, "confidence": 0.9}], "overall": "consistent"}))
    if "Translate every value into" in sys:
        body = json.loads(msgs[1]["content"])
        return 200, _reply(json.dumps({"t": {k: "[tr] " + v for k, v in body.items()}}))
    if "Translate the patient-facing text" in sys:
        body = json.loads(msgs[1]["content"])
        return 200, _reply(json.dumps({"summary": "[hi] " + body["summary"], "items": [{**i, "explanation": "[hi] " + i["explanation"]} for i in body["items"]]}))
    if "drafting a formal claim" in sys:
        return 200, _reply(json.dumps({"subject": "Appeal for [CLAIM_NO]", "salutation": "To the GRO", "paragraphs": ["I, [PATIENT_NAME], appeal."],
                                       "grounds": [{"title": "Moratorium", "text": "Cover continuous > 60 months", "cites": ["IN-MORATORIUM"]}],
                                       "relief": "Settle the claim", "enclosures": ["Policy"], "closing": "[PATIENT_NAME]"}))
    if "policy-wording extractor" in sys:   # small local models return nested / stringy shapes — must not crash
        return 200, _reply(json.dumps({"insurer": {"value": "Demo General Insurance"}, "product": {"value": "Demo Policy", "quote": "x"},
                                       "uin": {"value": "DEMOHLIP26001V012627"}, "sum_insured": "Rs. 5,00,000",
                                       "room_rent": {"kind": "pct_si", "value": "1%", "cite": {"page": "2", "quote": "Room rent, boarding and nursing expenses are payable up to 1% of the Sum Insured per day."}},
                                       "copays": [{"pct": ""}, {"pct": "10%", "age_min": "61 years"}], "waiting_periods": [{"kind": "ped", "months": "36 months"}],
                                       "exclusions": ["Excl05"], "intimation_emergency_hours": "24 hours"}))
    if "extractor for hospital bills" in sys:
        return 200, _reply(json.dumps({"hospital": "Mock Hospital", "admission_date": "2026-10-20", "stated_total": 1000, "line_items": [{"description": "Room", "category": "room", "amount": 1000, "page": 1}]}))
    if "answering a policyholder" in sys:
        return 200, _reply("Room rent is capped at 1% of SI per day [3.1].")
    return 200, _reply(json.dumps({"ok": True}))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({"data": [{"id": "accounts/fireworks/models/llama-v3p1-8b"}, {"id": "accounts/fireworks/models/muse-spark-open-1026"}]}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        req = json.loads(self.rfile.read(n))
        CALLS.append(req)
        code, body = respond(req)
        if TRUNCATE and code == 200 and body["choices"][0]["message"].get("content"):
            c = body["choices"][0]["message"]["content"]
            body["choices"][0]["message"]["content"] = c[: max(1, len(c) * 2 // 3)]
            body["choices"][0]["finish_reason"] = "length"
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def log_message(self, *a):
        pass


def start(port: int = 8765) -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
