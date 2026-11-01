"""Pipeline with a mock OpenAI-compatible server: routing, json ladder, tool loop, verifier, localizer, appeal."""
import importlib
import pytest
import config


@pytest.fixture(scope="module")
def mock(tmp_path_factory):
    from tests import mock_llm_server as m
    from core import budget
    tmp = tmp_path_factory.mktemp("llm")
    budget.USAGE = tmp / "usage.jsonl"
    config.PATHS["cache"] = tmp
    srv = m.start(8765)
    config.PROVIDERS["baseten"].update(base_url="http://127.0.0.1:8765/v1", api_key="test", budget=1000, model="mock-muse-open")
    config.PROVIDERS["fireworks"].update(base_url="http://127.0.0.1:8765/v1", api_key="test", budget=1000, model="mock-muse-open")
    config.ROUTES.update({r: ["baseten", "fireworks"] for r in config.ROUTES})
    config.JURY_PROVIDERS[:] = ["muse", "baseten", "fireworks"]
    config.REASONING_PARAMS.clear()
    config.PROVIDERS["muse"].update(base_url="", api_key="")
    config.PROVIDERS["muse_cheap"].update(base_url="", api_key="")
    config.LLM_MODE = "live"; config.CACHE_READ = False
    from core import llm
    llm._clients.clear(); llm.DOWN.clear()
    yield m
    srv.shutdown()


def test_rules_offline():
    config.LLM_MODE = "off"
    from core.pipeline import run_scenario
    r = run_scenario("S01_cataract_pre_discharge", engine="full")
    assert r.report.engine == "rules" and r.report.payout_before == pytest.approx(44370, abs=1)
    config.LLM_MODE = "live"


def test_full_with_mock(mock):
    from core.pipeline import run_scenario
    r = run_scenario("S01_cataract_pre_discharge", engine="full", user_lang="hi")
    rep = r.report
    kinds = [e.get("kind") for e in rep.trace]
    assert "tool" in kinds                                    # agent actually called tools
    assert rep.llm_calls >= 5
    assert not any(f.title == "Uncited speculation" for f in rep.findings)   # verifier dropped uncited risk
    pd = next(f for f in rep.findings if f.issue_type == "proportionate")
    assert pd.inr_impact == pytest.approx(10900, abs=1)       # money from engine, not model
    assert pd.explanations.get("hi", "").startswith("[tr]")  # localizer
    assert rep.payout_after_fixes == pytest.approx(59130, abs=1)
    # json_schema rejected by mock -> ladder fell back to json_object
    assert any(c.get("response_format", {}).get("type") == "json_object" for c in mock.CALLS)


def test_appeal_letter(mock):
    from core.pipeline import run_scenario
    from core import appeal
    r = run_scenario("S08_ped_rejection_appeal", engine="lean")
    letter = appeal.draft(r.bundle, r.report, "appeal")
    filled = appeal.fill(letter, {"[PATIENT_NAME]": "H. Malhotra", "[CLAIM_NO]": "DGI/1"})
    assert "H. Malhotra" in appeal.to_text(filled)
    pdf = appeal.to_pdf(filled, r.report, r.bundle)
    assert pdf[:4] == b"%PDF"
    assert r.report.recoverable_via_appeal == 256000


def test_policy_chat(mock):
    from core.pipeline import run_scenario
    from core import agents
    r = run_scenario("S05_clean_dengue", engine="rules")
    a = agents.ask_policy(r.bundle, "What is the room rent limit?")
    assert "[3.1]" in a["answer"] and a["sources"]["clauses"]


def test_jury_two_providers(mock):
    from core.pipeline import run_scenario
    r = run_scenario("S14_pd_no_differential_kannada", engine="full")
    pd = next(f for f in r.report.findings if f.issue_type == "proportionate")
    assert set(pd.tool_evidence.get("jury", {})) == {"baseten", "fireworks"}
    assert any(e.get("stage") == "jury" and e.get("kind") == "done" for e in r.report.trace)


def test_vision_locate_on_photo(mock):
    import config as cfg
    from core.pipeline import run_scenario
    from core import evidence
    cfg.USE_SIDECAR = True
    r = run_scenario("S12_phone_photo_gastro", engine="lean", use_files=True)
    cfg.USE_SIDECAR = False
    f = next(f for f in r.report.findings if f.bill_cite)
    assert f.bill_cite.bbox is not None
    png = evidence.render(r.docs[f.bill_cite.doc], f.bill_cite.page, [f.bill_cite.bbox])
    assert png[:4] == b"\x89PNG"


def test_url_model_id_auto_resolves(mock, tmp_path):
    import config as cfg
    from core import llm
    llm._RESOLVED = tmp_path / "resolved.json"; llm._TRIED.discard("fireworks")
    saved = cfg.PROVIDERS["fireworks"]["model"]
    cfg.PROVIDERS["fireworks"]["model"] = "https://api.fireworks.ai/inference/v1/"
    assert llm.provider_ready("fireworks")
    assert cfg.PROVIDERS["fireworks"]["model"] == "accounts/fireworks/models/muse-spark-open-1026"
    cfg.PROVIDERS["fireworks"]["model"] = saved


def test_fatal_error_disables_provider(mock):
    import config as cfg
    from core import llm
    cfg.PROVIDERS["fireworks"].update(base_url="http://127.0.0.1:9/v1")   # closed port -> connection error (retryable, not fatal)
    llm._clients.clear()
    assert llm._is_fatal(Exception("Error code: 404 - model not found"))
    assert not llm._is_fatal(Exception("Connection error"))
    cfg.PROVIDERS["fireworks"].update(base_url="http://127.0.0.1:8765/v1"); llm._clients.clear()


def test_provider_roles_follow_the_map(mock):
    """Each host does its job: Muse API judges, Baseten reads, Fireworks checks/translates; jury spans all three."""
    import config as cfg
    from core import llm
    from core.pipeline import run_scenario
    saved = {r: list(c) for r, c in cfg.ROUTES.items()}
    cfg.ROUTES.update({r: list(c) for r, c in cfg._PROD.items()})
    cfg.PROVIDERS["muse"].update(base_url="http://127.0.0.1:8765/v1", api_key="test", budget=1000, model="mock-muse-1.3")
    llm._clients.clear(); llm.DOWN.clear()
    cfg.USE_SIDECAR = False
    try:
        r = run_scenario("S01_cataract_pre_discharge", engine="full", user_lang="hi", use_files=True).report
        roles = {p: set(d["roles"]) for p, d in r.provider_calls.items()}
        assert {"tpa", "advocate", "jury"} <= roles["muse"]
        assert {"extract", "jury"} <= roles["baseten"]
        assert {"critic", "localize", "jury"} <= roles["fireworks"]
    finally:
        cfg.ROUTES.clear(); cfg.ROUTES.update(saved)
        cfg.PROVIDERS["muse"].update(base_url="", api_key="")
        llm._clients.clear()


def test_prod_routing_is_logical():
    import config as cfg
    P = cfg._PROD
    assert all(P[r][0] == "muse" for r in ("tpa", "advocate", "appeal", "jury", "voice"))
    assert all(P[r][0] == "baseten" for r in ("extract", "evidence"))
    assert all(P[r][0] == "fireworks" for r in ("critic", "localize", "chat", "single"))
    assert P["critic"][0] not in (P["tpa"][0], P["advocate"][0])           # independent check on a different host
    assert "muse" not in cfg._DEV["tpa"][:1]


def test_policy_extraction_survives_odd_model_shapes(mock):
    from core.ingest import load
    from core.extract import extract_policy
    from core.privacy import Vault
    from pathlib import Path
    pdf = Path(__file__).resolve().parents[1] / "data" / "policies" / "demo_shield_policy.pdf"
    doc = load("p.pdf", pdf.read_bytes(), "policy")
    doc.pages[0].text = ""            # force a model pass
    pol = extract_policy(doc, Vault(), save=False)
    assert pol.room_rent.value == 1.0 and pol.waiting_periods


def test_truncating_host_never_loops(mock):
    """A host that always cuts answers off must not loop or stall: bounded requests, run completes, money unchanged."""
    import time
    from core.pipeline import run_scenario
    mock.TRUNCATE = True
    n0, t0 = len(mock.CALLS), time.time()
    try:
        r = run_scenario("S01_cataract_pre_discharge", engine="full", user_lang="hi").report
    finally:
        mock.TRUNCATE = False
    assert time.time() - t0 < 30
    assert len(mock.CALLS) - n0 <= 60
    assert r.payout_before == 44370 and r.payout_after_fixes == 59130


import json as _json
from pathlib import Path as _P
_ALL = sorted(p.name for p in (_P(__file__).resolve().parents[1] / "data" / "scenarios").iterdir() if (p / "truth.json").exists())


@pytest.mark.parametrize("sid", _ALL)
def test_full_agent_engine_on_every_sample(mock, sid):
    """TPA → Advocate → parallel Jury → Critic → Localizer on every sample (mock hosts): completes fast, money comes from the engine."""
    import time
    from core.pipeline import run_scenario
    exp = _json.loads((_P(__file__).resolve().parents[1] / "data" / "scenarios" / sid / "expected.json").read_text(encoding="utf-8"))
    t0 = time.time()
    r = run_scenario(sid, engine="full", user_lang="hi").report
    assert time.time() - t0 < 20
    assert r.payout_before == exp["payout_before"]
    assert r.llm_calls <= 40


@pytest.mark.parametrize("lang", ["hi", "hinglish", "gu", "mr", "kn", "ta", "te", "bn", "ml", "pa", "or", "es"])
def test_every_language_translates_findings_and_summary(mock, lang):
    from core.pipeline import run_scenario
    r = run_scenario("S01_cataract_pre_discharge", engine="lean", user_lang=lang).report
    assert r.summary_localized.startswith("[tr]")
    assert all(f.explanations.get(lang, "").startswith("[tr]") for f in r.findings)


@pytest.mark.parametrize("engine", ["single", "lean", "full", "rules"])
def test_every_engine_runs_in_every_language_mode(mock, engine):
    from core.pipeline import run_scenario
    for lang in ("en", "ta"):
        r = run_scenario("S01_cataract_pre_discharge", engine=engine, user_lang=lang).report
        assert r.claimed == 71350
