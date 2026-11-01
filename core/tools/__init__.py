"""Tool registry exposed to Muse agents (OpenAI function-calling specs bound to one claim bundle)."""
from __future__ import annotations
from typing import Any, Callable
from core.tools import checks as C
from core.tools.payout import compute_payout
from core import kb

CATS = ["room", "icu", "nursing", "consultation", "surgeon", "anaesthesia", "ot", "procedure", "implant", "pharmacy",
        "consumable", "investigation", "package", "ambulance", "misc"]


def _spec(name: str, desc: str, props: dict, req: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req or []}}}


def registry(b: C.Bundle, allow: set[str] | None = None) -> tuple[list[dict], dict[str, Callable[..., Any]]]:
    idx = b.clause_index or kb.ClauseIndex(b.policy.clauses)
    code = b.ctx.jurisdiction

    def recategorize_item(item_id: str, category: str, reason: str = "") -> dict:
        if category not in CATS:
            return {"error": f"category must be one of {CATS}"}
        it = next((i for i in b.bill.line_items if i.id == item_id), None)
        if not it:
            return {"error": "no such item"}
        old = it.category
        it.category = category  # type: ignore[assignment]
        p = compute_payout(b)
        return {"item": item_id, "from": old, "to": category, "reason": reason, "new_payout": p.payout,
                "deductions": [(d.id, d.amount) for d in p.deductions]}

    def waiting_period_check(condition: str = "") -> dict:
        return C.waiting_period_check(b, condition)

    def sublimit_check(procedure: str = "") -> dict:
        return C.sublimit_check(b, procedure)

    def ped_check() -> dict:
        return C.ped_check(b)

    def get_clause(clause_id: str = "", query: str = "") -> dict:
        c = idx.get(clause_id) if clause_id else None
        if c:
            return {"clause_id": c.id, "page": c.page, "title": c.title, "quote": c.text[:1200]}
        hits = idx.search(query or clause_id, 3)
        return {"matches": [{"clause_id": h.id, "page": h.page, "title": h.title, "quote": h.text[:600]} for h in hits]}

    def search_rules(query: str) -> dict:
        return {"rules": [{"id": r["id"], "rule": r["rule"], "source": r["source"], "overrides_policy": r.get("overrides_policy")}
                          for r in kb.search_rules(code, query, 4)]}

    def search_precedents(query: str) -> dict:
        return {"precedents": [{"id": p["id"], "illustrative": p.get("illustrative", False), "issue_type": p.get("issue_type"),
                                "complaint": p.get("complaint"), "decision": p.get("decision"), "reasoning": p.get("reasoning_summary"),
                                "office": p.get("office"), "year": p.get("year")} for p in kb.search_precedents(code, query, 3)]}

    def non_payable_lookup(description: str) -> dict:
        hits = [n for n in kb.non_payables(code) if C.norm(n["item"]) in C.norm(description) or any(C.norm(a) in C.norm(description) for a in n.get("aliases", []))]
        return {"matches": [{"item": h["item"], "list": h["list"], "treatment": h["treatment"]} for h in hits[:3]]}

    def compute_payout_tool() -> dict:
        p = compute_payout(b)
        return {"claimed": p.claimed, "payout": p.payout, "copay": p.copay, "deductions": [d.model_dump() for d in p.deductions]}

    fns = {"recategorize_item": recategorize_item, "waiting_period_check": waiting_period_check, "sublimit_check": sublimit_check,
           "ped_check": ped_check, "get_clause": get_clause, "search_rules": search_rules, "search_precedents": search_precedents,
           "non_payable_lookup": non_payable_lookup, "compute_payout": compute_payout_tool}
    specs = [
        _spec("recategorize_item", "Correct the category of a mis-classified bill line (affects proportionate deduction and non-payable logic). Returns new deductions.",
              {"item_id": {"type": "string"}, "category": {"type": "string", "enum": CATS}, "reason": {"type": "string"}}, ["item_id", "category"]),
        _spec("waiting_period_check", "Check initial/specific waiting periods for a condition or procedure name (e.g. 'cataract').", {"condition": {"type": "string"}}),
        _spec("sublimit_check", "Check policy sub-limits for a procedure name.", {"procedure": {"type": "string"}}),
        _spec("ped_check", "Pre-existing disease analysis: conditions in records, declaration, moratorium status.", {}),
        _spec("get_clause", "Fetch exact policy clause text by id or by search query.", {"clause_id": {"type": "string"}, "query": {"type": "string"}}),
        _spec("search_rules", "Search regulator rules (they override policy wording when marked).", {"query": {"type": "string"}}, ["query"]),
        _spec("search_precedents", "Search ombudsman awards / precedents for an issue.", {"query": {"type": "string"}}, ["query"]),
        _spec("non_payable_lookup", "Check if an item description is on the regulator non-payable lists.", {"description": {"type": "string"}}, ["description"]),
        _spec("compute_payout", "Recompute payout and all deductions (the only source of money figures).", {}),
    ]
    if allow:
        specs = [s for s in specs if s["function"]["name"] in allow]
        fns = {k: v for k, v in fns.items() if k in allow}
    return specs, fns
