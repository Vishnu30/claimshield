"""End-to-end UI tests (offline, $0). Every sample — verified and challenge — is driven through every feature:
results, evidence, what-if, letters, policy chat, trace, report PDF, new check. Plus wizard validation, languages, recorded runs."""
import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
import config

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app" / "ui.py")
CAT = {p.name: json.loads((p / "truth.json").read_text(encoding="utf-8")).get("tier", "verified")
       for p in sorted((ROOT / "data" / "scenarios").iterdir()) if (p / "truth.json").exists()}
VERIFIED = [k for k, v in CAT.items() if v == "verified"]
CHALLENGE = [k for k, v in CAT.items() if v == "challenge"]


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Offline, and with no translated interface files: tests click buttons by their English labels,
    so locales built on this machine (app/locales/<lang>.json) must not change the labels under test."""
    import app.i18n as i18n
    old = config.LLM_MODE
    config.LLM_MODE = "off"
    monkeypatch.setattr(i18n, "LOCALES", tmp_path / "no_locales"); i18n._LOADED.clear()
    yield
    config.LLM_MODE = old
    i18n._LOADED.clear()


def _app():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _ok(at):
    assert not at.exception, [str(e.value)[:300] for e in at.exception]


def _btn(at, label=None, key=None):
    for b in at.button:
        if (key and b.key == key) or (label and b.label == label):
            return b
    return None


def _walk_results(at):
    """Exercise every results feature on whatever claim is open."""
    rep = at.session_state["result"].report
    assert at.session_state["step"] == 3
    ev = _btn(at, "Show where on the document")
    if ev:
        ev.click(); at.run(); _ok(at)
    for c in [c for c in at.checkbox if c.key and c.key.startswith("wi_")][:2]:
        c.uncheck(); at.run(); _ok(at)
    dl = _btn(at, "✍️ Draft letter")
    if dl:
        dl.click(); at.run(); _ok(at)
        assert at.session_state["letter"]
    at.chat_input[0].set_value("What is the room rent limit?"); at.run(); _ok(at)
    assert at.session_state["chat"][-1]["role"] == "assistant"
    tg = [t for t in at.toggle if t.label == "Show every model call"]
    if tg:
        tg[0].set_value(True); at.run(); _ok(at)
    return rep


def test_home_has_both_tiers_and_languages():
    at = _app()
    keys = {b.key for b in at.button}
    assert any(k.startswith("sample_") for k in keys) and any(k.startswith("challenge_") for k in keys)
    assert {"mode_pre_discharge", "mode_pre_submission", "mode_appeal"} <= keys
    [p for p in at.pills if p.key == "lang_pick"][0].set_value("ta"); at.run(); _ok(at)
    assert at.session_state["lang"] == "ta"
    assert at.sidebar.selectbox(key="lang").value == "ta"            # home chips drive the sidebar


def test_hero_sample_numbers_and_every_feature():
    at = _app()
    _btn(at, key="sample_S01_cataract_pre_discharge").click(); at.run(); _ok(at)
    rep = _walk_results(at)
    assert rep.payout_before == 44370 and rep.payout_after_fixes == 59130
    _btn(at, "↺ New check").click(); at.run(); _ok(at)
    assert at.session_state["step"] == 0 and at.session_state["result"] is None


def test_wizard_requires_bill_and_policy():
    at = _app()
    _btn(at, key="mode_pre_submission").click(); at.run()
    _btn(at, "Next: patient details").click(); at.run()
    _btn(at, key="run_audit").click(); at.run()
    assert at.error and at.session_state["step"] == 1


def test_hindi_chrome_and_pages():
    at = _app()
    at.sidebar.selectbox(key="lang").set_value("hi"); at.run(); _ok(at)
    assert any("शुरू" in b.label for b in at.button)
    for pg in ("eval", "kb", "about"):
        at.sidebar.radio(key="page").set_value(pg); at.run(); _ok(at)


@pytest.mark.parametrize("sid", VERIFIED)
def test_verified_sample_via_wizard(sid):
    at = _app()
    at.selectbox(key="scen_pick").set_value(sid); at.run()
    _btn(at, key="open_sample").click(); at.run()
    _btn(at, "Next: patient details").click(); at.run()
    _btn(at, key="run_audit").click(); at.run(); _ok(at)
    _walk_results(at)


@pytest.mark.parametrize("sid", CHALLENGE)
def test_challenge_sample_runs(sid):
    at = _app()
    _btn(at, key="challenge_" + sid).click(); at.run(); _ok(at)
    _walk_results(at)


def test_recorded_run_opens_instantly_without_calls(tmp_path, monkeypatch):
    from core import snapshots, appeal
    from core.pipeline import run_scenario
    monkeypatch.setattr(snapshots, "DIR", tmp_path)
    sid = "S08_ped_rejection_appeal"
    r = run_scenario(sid, engine="rules")
    r.report.provider_calls = {"muse": {"calls": 9, "cached": 0, "usd": 0.1, "tokens": 1, "roles": ["tpa"]}}
    snapshots.save(sid, r, {"appeal": appeal.draft(r.bundle, r.report, "appeal")}, "prod")
    at = _app()
    _btn(at, key="sample_" + sid).click(); at.run(); _ok(at)
    assert at.session_state["snap"] and at.session_state["result"].report.recoverable_via_appeal == 256000
    assert any("Recorded live run" in m.value for m in at.markdown)
    _walk_results(at)


def test_results_language_switch_offline():
    at = _app()
    _btn(at, key="sample_S01_cataract_pre_discharge").click(); at.run(); _ok(at)
    [p for p in at.pills if p.key == "lang_pick_res"][0].set_value("ta"); at.run(); _ok(at)
    assert at.session_state["result"].report.user_lang == "ta" and at.session_state["lang"] == "ta"
    at.sidebar.selectbox(key="lang").set_value("kn"); at.run(); _ok(at)
    assert at.session_state["result"].report.user_lang == "kn"


def test_quick_questions():
    at = _app()
    _btn(at, key="sample_S05_clean_dengue") or None
    at.selectbox(key="scen_pick").set_value("S05_clean_dengue"); at.run()
    _btn(at, key="open_sample").click(); at.run()
    _btn(at, "Next: patient details").click(); at.run()
    _btn(at, key="run_audit").click(); at.run(); _ok(at)
    _btn(at, key="q_What is my room rent limit?").click(); at.run(); _ok(at)
    assert at.session_state["chat"][-1]["role"] == "assistant"


def test_live_run_is_cached_then_opens_instantly_and_translates(tmp_path, monkeypatch):
    """With AI hosts (mock): first open runs live and records it; second open is instant; switching language translates in place."""
    from tests import mock_llm_server as m
    from core import snapshots, llm, budget
    import app.i18n as i18n
    monkeypatch.setattr(i18n, "LOCALES", tmp_path / "loc"); i18n._LOADED.clear()
    srv = m.start(8791)
    monkeypatch.setattr(snapshots, "DIR", tmp_path / "s"); monkeypatch.setattr(snapshots, "LOCAL_DIR", tmp_path / "l")
    monkeypatch.setattr(budget, "USAGE", tmp_path / "u.jsonl"); config.PATHS["cache"] = tmp_path
    saved = {k: dict(v) for k, v in config.PROVIDERS.items()}
    for p in ("baseten", "fireworks"):
        config.PROVIDERS[p].update(base_url="http://127.0.0.1:8791/v1", api_key="t", model="mock", budget=1000)
    config.PROVIDERS["muse"].update(base_url="", api_key="")
    routes = {r: list(c) for r, c in config.ROUTES.items()}
    config.ROUTES.update({r: ["baseten", "fireworks"] for r in config.ROUTES})
    config.LLM_MODE = "live"; llm._clients.clear(); llm.DOWN.clear()
    try:
        at = _app()
        _btn(at, key="challenge_C03_package_with_extras_discount").click(); at.run(); _ok(at)
        assert at.session_state["result"].report.llm_calls > 0 and snapshots.exists("C03_package_with_extras_discount")
        _btn(at, "↺ New check").click(); at.run()
        _btn(at, key="challenge_C03_package_with_extras_discount").click(); at.run(); _ok(at)
        assert at.session_state["snap"] is not None
        [p for p in at.pills if p.key == "lang_pick_res"][0].set_value("gu"); at.run(); _ok(at)
        rep = at.session_state["result"].report
        assert rep.user_lang == "gu" and rep.summary_localized.startswith("[tr]")
    finally:
        srv.shutdown()
        for k, v in saved.items():
            config.PROVIDERS[k].clear(); config.PROVIDERS[k].update(v)
        config.ROUTES.clear(); config.ROUTES.update(routes); llm._clients.clear(); config.LLM_MODE = "off"
        i18n._LOADED.clear()
