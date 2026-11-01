"""Deterministic engine tests (0 credits)."""
import json
import pytest
import config
from config import PATHS
from core import kb
from core.schemas import Bill, Discharge, Rejection, ClaimContext
from core.tools.checks import Bundle, months_between, parse_date, reconcile_totals, non_payable_check
from core.tools.payout import compute_payout
from core.detectors import detect
from core.privacy import Vault
from core.fmt import money


def bundle(sid):
    t = json.loads((PATHS["scenarios"] / sid / "truth.json").read_text(encoding="utf-8"))
    return Bundle(policy=kb.load_policy(t["policy_id"]), bill=Bill.model_validate(t["bill"]), ctx=ClaimContext.model_validate(t["ctx"]),
                  discharge=Discharge.model_validate(t["discharge"]), rejection=Rejection.model_validate(t["rejection"]) if t.get("rejection") else None)


def test_dates():
    assert months_between(parse_date("2023-04-01"), parse_date("2026-10-20")) == 42
    assert parse_date("20/10/2026").isoformat() == "2026-10-20"


def test_money_format():
    assert money(256000) == "₹2,56,000"
    assert money(1234567, "$", "western") == "$1,234,567"


def test_s01_numbers():
    b = bundle("S01_cataract_pre_discharge")
    p = compute_payout(b)
    d = {x.id: x.amount for x in p.deductions}
    assert d["D-UNITEM"] == 1700 and d["D-NP"] == 3150 and d["D-NP-KIT"] == 3800 and d["D-ROOM"] == 2500
    assert d["D-PD"] == pytest.approx(10900, abs=1)
    assert p.payout == pytest.approx(44370, abs=1)
    p2 = compute_payout(b, waive={"D-UNITEM", "D-NP-KIT", "D-PD"})
    assert p2.payout == pytest.approx(59130, abs=1)


def test_reconcile_and_dups():
    b = bundle("S07_billing_errors")
    r = reconcile_totals(b)
    assert len(r["duplicates"]) == 1 and r["arithmetic_errors"][0]["excess"] == 500
    p = compute_payout(b, waive={"D-DUP", "D-ARITH"})
    assert p.patient_pays == 0 and p.payout == 37900


def test_non_payables_matching():
    b = bundle("S01_cataract_pre_discharge")
    m = {x["item_id"]: x for x in non_payable_check(b)["matches"]}
    assert m["K1"]["unitemized_kit"] and "I1" not in m and "S1" not in m
    assert m["X1"]["list"] == "I"


def test_waiting_and_portability():
    assert compute_payout(bundle("S02_hernia_waiting_valid")).payout == 0
    b = bundle("S06_hernia_portability")
    assert compute_payout(b).payout == 0 and compute_payout(b, waive={"D-WAIT"}).payout == 70500


def test_moratorium_appeal():
    b = bundle("S08_ped_rejection_appeal")
    F = detect(b)
    f = next(x for x in F if x.issue_type == "wrongful_rejection")
    assert f.status == "contestable" and f.irdai_cite and "IN-MORATORIUM" in f.irdai_cite.doc


def test_clean_control():
    assert detect(bundle("S05_clean_dengue")) == []


def test_pii_vault():
    v = Vault()
    t = v.redact("Patient Name: Ramesh Patel\nMobile 9876543210 Aadhaar 1234 5678 9012 PAN ABCDE1234F")
    assert "9876543210" not in t and "Ramesh" not in t and "ABCDE1234F" not in t
    assert "Ramesh Patel" in v.restore(t)


def test_us_pack_disables_room_rent():
    b = bundle("S01_cataract_pre_discharge")
    b.ctx.jurisdiction = "US"
    assert not any(d.id in ("D-PD", "D-NP") for d in compute_payout(b).deductions)


def test_all_scenarios_rules_engine():
    import config as cfg
    from eval.run_eval import run_all
    old = cfg.LLM_MODE; cfg.LLM_MODE = "off"
    r = run_all(["rules"])["rules"]
    cfg.LLM_MODE = old
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["money_error"] == 0
    assert len(r["per_scenario"]) == 36


def test_short_settlement_and_generic_reject():
    from core.pipeline import run_scenario
    import config as cfg
    cfg.LLM_MODE = "off"
    r = run_scenario("S20_short_settlement_pd_telugu").report
    assert r.recoverable_via_appeal > 0 and any(f.issue_type == "short_settlement" for f in r.findings)
    r = run_scenario("S22_late_intimation_rejection_appeal").report
    assert r.recoverable_via_appeal == 71000
    cfg.LLM_MODE = "live"
