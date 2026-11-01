"""Orchestrator: S1 Ingest → S2 Extract → S3 TPA → S4 Advocate → S5 Verify(+Critic) → S6 Evidence → S7 Report.
Credit plan per run (engine=full): extraction 2–4 calls (cached by file hash), TPA ≤6 turns, Advocate ≤6 turns,
Jury 1 call per provider (Muse API, Baseten, Fireworks), Critic 1, Localizer 1, vision-locate ≤6 (scans only)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional
import time
import config
from core import llm, kb, extract, agents, verifier, evidence, report
from core.ingest import load, Doc
from core.privacy import Vault
from core.schemas import ClaimContext, PolicyRules, Bill, Discharge, Rejection
from core.tools.checks import Bundle
from core.detectors import detect, evidence_pack

ENGINES = {"full": "TPA + Advocate + 3-provider Jury + Critic (best)", "lean": "Rules + Advocate (≈40% of full cost)",
           "rules": "Rules engine only (0 credits)", "single": "Single Muse pass (ablation baseline)"}


@dataclass
class ClaimInputs:
    ctx: ClaimContext
    policy_id: Optional[str] = None
    policy_file: Optional[tuple[str, bytes]] = None
    bills: list[tuple[str, bytes]] = field(default_factory=list)
    discharge: Optional[tuple[str, bytes]] = None
    rejection: Optional[tuple[str, bytes]] = None
    engine: str = "full"
    password: Optional[str] = None
    # pre-extracted objects (eval / tests)
    policy: Optional[PolicyRules] = None
    bill: Optional[Bill] = None
    discharge_obj: Optional[Discharge] = None
    rejection_obj: Optional[Rejection] = None


@dataclass
class RunResult:
    report: "report.ClaimReport"
    bundle: Bundle
    docs: dict[str, Doc]
    vault: Vault
    seconds: float


def run(inp: ClaimInputs, on_event: Optional[Callable[[dict], None]] = None) -> RunResult:
    t0 = time.time()
    stats = llm.RunStats(on_event)
    llm.set_stats(stats)
    vault = Vault()
    docs: dict[str, Doc] = {}
    E = lambda stage, text, **kw: stats.emit({"stage": stage, "kind": "info", "text": text, **kw})
    engine = inp.engine if (inp.engine == "rules" or llm.available()) else "rules"
    if engine != inp.engine:
        E("router", "No LLM provider configured or LLM_MODE=off → rules engine (0 credits)")

    # S1 ingest
    for name, data in inp.bills:
        docs[name] = load(name, data, "bill", inp.password)
    if inp.discharge:
        docs["discharge"] = load(*inp.discharge, "discharge", inp.password)
    if inp.rejection:
        docs["rejection"] = load(*inp.rejection, "rejection", inp.password)
    if inp.policy_file:
        docs["policy"] = load(*inp.policy_file, "policy", inp.password)
    if docs:
        E("ingest", f"{len(docs)} document(s): " + ", ".join(f"{d.kind}:{len(d.pages)}p{' scan' if d.scanned else ''}" for d in docs.values()))

    # S2 extract
    pol = inp.policy
    if pol is None:
        if inp.policy_id:
            pol = kb.load_policy(inp.policy_id); E("extract", f"policy from library: {pol.product} (0 credits)")
        elif "policy" in docs:
            pol = extract.extract_policy(docs["policy"], vault, inp.ctx.jurisdiction)
        else:
            raise ValueError("A policy is required (library or upload).")
    if "policy" not in docs and pol.source_file:
        p = config.ROOT / pol.source_file
        if p.exists():
            docs["policy"] = load(p.name, p.read_bytes(), "policy")
    bill = inp.bill
    if bill is None:
        bills = [extract.extract_bill(docs[n], vault) for n, _ in inp.bills]
        for bl, (n, _) in zip(bills, inp.bills):
            for it in bl.line_items:
                it.doc = n
        if not bills:
            raise ValueError("At least one bill is required.")
        bill = extract.merge_bills(bills)
        if not bill.line_items:
            raise ValueError("No billed line items could be read from the bill. Is this the hospital's final itemized bill? "
                             "If it is a photo, retake it flat, in good light, with the full table visible.")
    dis = inp.discharge_obj or (extract.extract_discharge(docs["discharge"], vault) if "discharge" in docs else Discharge())
    rej = inp.rejection_obj or (extract.extract_rejection(docs["rejection"], vault) if "rejection" in docs else None)
    ctx = inp.ctx
    if ctx.narrative and config.REDACT_PII:
        ctx = ctx.model_copy(update={"narrative": vault.redact(ctx.narrative)})
    if vault.count:
        E("privacy", f"{vault.count} personal identifier(s) masked before any model call")
    b = Bundle(policy=pol, bill=bill, ctx=ctx, discharge=dis, rejection=rej, clause_index=kb.ClauseIndex(pol.clauses))

    # S3–S5
    ev = evidence_pack(b)
    E("tools", f"deterministic checks run: {len(ev)} tools")
    loc_future = None
    if engine == "single":
        try:
            findings = agents.single_pass(b)
        except llm.LLMUnavailable:
            findings = detect(b, ev)
        findings = verifier.verify(b, findings)
    else:
        findings = detect(b, ev)
        E("rules", f"rules engine: {len(findings)} candidate finding(s)")
        if engine == "full":
            findings = agents.tpa_adjudicate(b, ev, findings)
        if engine in ("full", "lean"):
            findings = agents.advocate(b, findings)
        if ctx.user_lang != "en" and engine in ("full", "lean"):
            # wording is final after the Advocate → translate WHILE the jury and critic run (statuses only)
            import contextvars
            from concurrent.futures import ThreadPoolExecutor
            _ex = ThreadPoolExecutor(max_workers=1)
            E("localize", f"translating to {ctx.user_lang} in parallel with the jury")
            loc_future = _ex.submit(contextvars.copy_context().run, agents.localize_findings, findings, ctx.user_lang)
        if engine == "full":
            findings = agents.jury_and_critic(b, findings)
        findings = verifier.verify(b, findings)
    E("verify", f"{len(findings)} verified finding(s); {sum(1 for f in findings if f.verified)} fully cited")

    # S6 evidence
    evidence.locate(findings, docs, {i.description: i.doc for i in b.bill.line_items})
    # S7 report + localization
    rep = report.build(b, findings, stats.trace, stats.cost, stats.calls, engine)
    if loc_future is not None:
        try:
            loc_future.result(timeout=120)
        except Exception:  # noqa: BLE001
            pass
        stats.flush()
        missing = [f for f in rep.findings if ctx.user_lang not in f.explanations]
        if missing:
            agents.localize_findings(missing, ctx.user_lang)         # findings added after the advocate (rare)
        from core.translate import translate
        tr = translate({"summary": rep.summary_en, **{f"a{i}": a["action"] for i, a in enumerate(rep.action_plan)}}, ctx.user_lang, "sum")
        rep.summary_localized = tr.get("summary", rep.summary_en)
        for i, a in enumerate(rep.action_plan):
            a.setdefault("action_localized", {})[ctx.user_lang] = tr.get(f"a{i}", a["action"])
        stats.emit({"stage": "localize", "kind": "done", "text": f"results written in {ctx.user_lang}"})
    elif ctx.user_lang != "en" and engine == "single":
        rep.findings, rep.summary_localized = agents.localize(rep.findings, rep.summary_en, ctx.user_lang)
    rep.trace, rep.cost_usd, rep.llm_calls = stats.trace, round(stats.cost, 4), stats.calls
    rep.provider_calls = stats.by_provider
    if engine != "rules" and stats.calls == 0:
        rep.engine = f"{engine} → rules fallback (no provider answered)"
    E("report", f"done in {time.time() - t0:.1f}s · {stats.calls} model call(s) ({stats.cached} cached) · ${stats.cost:.4f}")
    return RunResult(report=rep, bundle=b, docs=docs, vault=vault, seconds=time.time() - t0)


def scenario_files(d) -> dict:
    bills = sorted([f for f in d.iterdir() if f.name.startswith("bill") and f.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png")])
    get = lambda n: next(((f.name, f.read_bytes()) for f in d.iterdir() if f.stem == n), None)
    return {"bills": [(f.name, f.read_bytes()) for f in bills], "discharge": get("discharge"), "rejection": get("rejection")}


def run_scenario(sid: str, engine: str = "rules", user_lang: str | None = None, on_event=None, use_files: bool = False) -> RunResult:
    import json
    d = config.PATHS["scenarios"] / sid
    truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    ctx = ClaimContext.model_validate({**truth["ctx"], "user_lang": user_lang or truth["ctx"].get("user_lang", "en")})
    fl = scenario_files(d)
    if use_files:
        inp = ClaimInputs(ctx=ctx, policy_id=truth["policy_id"], bills=fl["bills"], discharge=fl["discharge"], rejection=fl["rejection"], engine=engine)
    else:
        bill = Bill.model_validate(truth["bill"])
        if fl["bills"]:
            parts = truth.get("bill_parts") or {}
            for it in bill.line_items:
                it.doc = next((n for n, pb in parts.items() if any(x["id"] == it.id for x in pb["line_items"])), fl["bills"][0][0])
        inp = ClaimInputs(ctx=ctx, policy_id=truth["policy_id"], engine=engine, bill=bill,
                          discharge_obj=Discharge.model_validate(truth["discharge"]),
                          rejection_obj=Rejection.model_validate(truth["rejection"]) if truth.get("rejection") else None,
                          bills=fl["bills"], discharge=fl["discharge"])
    return run(inp, on_event)
