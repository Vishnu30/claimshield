"""Muse studies script: runs against mock hosts, writes results, and updates README between markers (on a temp copy)."""
import argparse, json, shutil
import pytest
import config
from tests import mock_llm_server as m
from core import llm, budget


@pytest.fixture()
def studies(tmp_path, monkeypatch):
    srv = m.start(8821)
    monkeypatch.setattr(budget, "USAGE", tmp_path / "u.jsonl"); config.PATHS["cache"] = tmp_path
    saved = {k: dict(v) for k, v in config.PROVIDERS.items()}
    for p in ("baseten", "fireworks", "muse"):
        config.PROVIDERS[p].update(base_url="http://127.0.0.1:8821/v1", api_key="t", model="mock", budget=1000)
    llm._clients.clear(); llm.DOWN.clear()
    import scripts.muse_studies as S
    root = tmp_path / "repo"; root.mkdir()
    shutil.copy(S.ROOT / "README.md", root / "README.md")
    monkeypatch.setattr(S, "OUT", tmp_path / "studies"); monkeypatch.setattr(S, "ROOT", root)
    from core import learned
    import scripts.muse_improve as IMP
    monkeypatch.setattr(learned, "DIR", tmp_path / "learned"); monkeypatch.setattr(IMP, "LOG", tmp_path / "learned" / "CHANGELOG.json")
    monkeypatch.setattr(IMP, "ROOT", root); monkeypatch.setattr(IMP, "_offline_tests_pass", lambda: True); learned.reload()
    yield S, root
    srv.shutdown()
    for k, v in saved.items():
        config.PROVIDERS[k].clear(); config.PROVIDERS[k].update(v)
    llm._clients.clear(); config.LLM_MODE = "off"; config.CACHE_READ = True


def test_studies_write_results_and_update_readme(studies):
    S, root = studies
    a = argparse.Namespace(max_muse=5, claims=3, workers=2, runs=2, files=False, no_improve=False)
    S.study_chat(a)
    S.study_redteam(a)
    S.study_robust(a)
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert readme.count("<!-- MUSE-STUDIES:START -->") == 1
    assert "Red team" in readme and "Ask your policy" in readme and "Coverage — every angle exercised" in readme
    S.report()                                                   # idempotent: still exactly one block
    assert (root / "README.md").read_text(encoding="utf-8").count("<!-- MUSE-STUDIES:START -->") == 1
    rt = json.loads((S.OUT / "redteam.json").read_text(encoding="utf-8"))
    assert all(v["money_unchanged"] == 1.0 for v in rt["attacks"].values())      # injected text can never move money
    assert "Self-improvement log" in (root / "README.md").read_text(encoding="utf-8")
    from core import learned
    learned.reload()


def test_improve_keeps_only_validated_changes(tmp_path, monkeypatch):
    """A proposed fix is kept only if it fixes a failure and breaks no control; otherwise every overlay is restored."""
    from core import learned
    import scripts.muse_improve as IMP
    import scripts.muse_studies as S
    monkeypatch.setattr(learned, "DIR", tmp_path / "learned"); monkeypatch.setattr(IMP, "LOG", tmp_path / "learned" / "CHANGELOG.json")
    learned.reload()
    proposal = {"diagnosis": "d", "changes": [{"kind": "prompt_note", "role": "chat", "text": "Always state the number and the clause id."},
                                              {"kind": "reason_pattern", "pattern": "(", "issue_type": "ped"},          # invalid regex → dropped
                                              {"kind": "prompt_note", "role": "chat", "text": "Claim S01 pays ₹5,000"}]}  # scenario-specific → dropped
    monkeypatch.setattr(IMP.llm, "complete_json", lambda *a, **k: (proposal, {}))
    fails = [{"case": 1, "context": {}}]
    assert IMP.improve("chat", fails, lambda c: True, [2], {"chat"}, {"prompt_note", "reason_pattern"}, S.Spend(5))
    assert learned.notes("chat") == ["Always state the number and the clause id."]
    assert not IMP.improve("chat", fails, lambda c: c == 1, [2], {"chat"}, {"prompt_note"}, S.Spend(5))   # control regresses → reverted
    assert learned.notes("chat") == ["Always state the number and the clause id."]
    assert [e["accepted"] for e in IMP.changelog()] == ["kept", "reverted"]
    learned.reload()


def test_learned_overlays_reach_the_backend(tmp_path, monkeypatch):
    from core import learned
    from core.tools import checks
    monkeypatch.setattr(learned, "DIR", tmp_path / "learned"); learned.reload()
    learned.save("reason_patterns", [{"pattern": r"co-?pay", "issue_type": "copay"}])
    learned.save("prompt_notes", {"tpa": ["Be strict."]})
    assert learned.reason_type("10% co-pay deducted") == "copay"
    assert learned.with_notes("tpa", "SYS").endswith("- Be strict.")
    learned.save("reason_patterns", []); learned.save("prompt_notes", {}); learned.reload()
