"""Edge-case uploads, deterministic parsers and JSON repair — the fallbacks that keep the app working offline."""
import json
from pathlib import Path
import pytest
import config
from core.llm import parse_json
from core.ingest import load, InputError
from core.doc_parser import parse_bill, parse_discharge, parse_rejection
from core.policy_parser import parse as parse_policy
from core.pipeline import run, ClaimInputs
from core.schemas import ClaimContext

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "samples" / "edge_cases"
S = ROOT / "data" / "scenarios"


@pytest.fixture(autouse=True)
def offline():
    old = config.LLM_MODE
    config.LLM_MODE = "off"
    yield
    config.LLM_MODE = old


def _run(bill_name, **kw):
    return run(ClaimInputs(ctx=ClaimContext(policy_start="2022-01-01", patient_age=40), policy_id="demo_shield",
                           bills=[(bill_name, (E / bill_name).read_bytes())], engine="full", **kw))


@pytest.mark.parametrize("name,msg", [("password_protected_bill.pdf", "password"), ("corrupt.pdf", "damaged"), ("iphone_photo.heic", "HEIC"),
                                      ("not_a_bill.pdf", "No billed line items"), ("blank_page.pdf", "scanned")])
def test_bad_uploads_give_clear_messages(name, msg):
    with pytest.raises(ValueError) as e:
        _run(name)
    assert msg.lower() in str(e.value).lower()


def test_password_unlock_and_rotated_and_long():
    assert _run("password_protected_bill.pdf", password="1234").report.claimed == 71350
    assert len(_run("rotated_bill.pdf").bundle.bill.line_items) == 15
    assert len(_run("long_bill_60_lines.pdf").bundle.bill.line_items) == 60


def test_bill_parser_matches_truth_on_all_digital_scenarios():
    for d in sorted(S.iterdir()):
        t = json.loads((d / "truth.json").read_text(encoding="utf-8"))
        for f in d.glob("bill*.pdf"):
            doc = load(f.name, f.read_bytes(), "bill")
            if doc.scanned:
                continue
            want = (t.get("bill_parts") or {}).get(f.name, t["bill"])
            got = parse_bill(doc)
            assert [i.amount for i in got.line_items] == [i["amount"] for i in want["line_items"]], d.name
            assert got.stated_total == want["stated_total"], d.name


def test_discharge_and_rejection_parsers():
    d = parse_discharge(load("d.pdf", (S / "S08_ped_rejection_appeal" / "discharge.pdf").read_bytes(), "discharge"))
    assert "myocardial" in d.diagnosis.lower() and d.admission_type == "emergency" and "hypertension" in d.comorbidities
    r = parse_rejection(load("r.pdf", (S / "S20_short_settlement_pd_telugu" / "rejection.pdf").read_bytes(), "rejection"))
    assert r.decision == "partial" and r.amount_paid and r.reasons


def test_policy_parser_both_demo_policies():
    import pymupdf
    p = parse_policy([pg.get_text() for pg in pymupdf.open(ROOT / "data/policies/demo_shield_policy.pdf")], "x")
    assert (p.room_rent.kind, p.room_rent.value) == ("pct_si", 1.0) and p.copays[0].age_min == 61
    assert {w.kind for w in p.waiting_periods} == {"initial", "specific", "ped"} and p.sublimits[0].limit == 70000
    q = parse_policy([pg.get_text() for pg in pymupdf.open(ROOT / "data/policies/demo_classic_policy.pdf")], "y")
    assert (q.room_rent.kind, q.room_rent.value) == ("fixed", 4000) and q.intimation_emergency_hours == 48
    c = parse_policy([pg.get_text() for pg in pymupdf.open(E / "policy_room_category.pdf")], "z")
    assert c.room_rent.kind == "category"


def test_uploaded_policy_offline_and_conditional_copay():
    r = run(ClaimInputs(ctx=ClaimContext(policy_start="2022-01-01", patient_age=40),
                        policy_file=("p.pdf", (E / "policy_room_category.pdf").read_bytes()),
                        bills=[("b.pdf", (S / "S01_cataract_pre_discharge" / "bill.pdf").read_bytes())], engine="full"))
    assert r.report.payout_detail_before.copay == 0                       # zone co-pay is conditional → not auto-applied
    assert any("entitlement" in f.title for f in r.report.findings)       # deluxe room vs single-private entitlement
    for f in (ROOT / "kb" / "policies").glob("upl_*.json"):
        f.unlink()


@pytest.mark.parametrize("raw,expect", [('Sure: {"a": [1, {"b": 2}], "c": "x', {"a": [1, {"b": 2}]}),
                                        ('<think>x {y}</think>{"ok": true}', {"ok": True}), ('```json\n{"k": 1}\n```', {"k": 1})])
def test_json_repair(raw, expect):
    assert parse_json(raw) == expect


@pytest.fixture
def si_less_policy():
    """A library policy whose wording has a %-of-SI room limit but no sum insured (common for real wordings)."""
    from core import kb
    p = kb.load_policy("demo_shield").model_copy(update={"policy_id": "test_no_si", "sum_insured": None})
    kb.save_policy(p)
    yield "test_no_si"
    (ROOT / "kb" / "policies" / "test_no_si.json").unlink(missing_ok=True)


def test_rotated_bill_with_policy_without_sum_insured(si_less_policy):
    """Regression: 'ClauseRef object has no attribute get'."""
    r = run(ClaimInputs(ctx=ClaimContext(policy_start="2022-01-01", patient_age=40), policy_id=si_less_policy,
                        bills=[("rotated_bill.pdf", (E / "rotated_bill.pdf").read_bytes())], engine="full")).report
    assert any("Sum insured needed" in f.title for f in r.findings)
    r2 = run(ClaimInputs(ctx=ClaimContext(policy_start="2022-01-01", patient_age=40, sum_insured=500000), policy_id=si_less_policy,
                         bills=[("rotated_bill.pdf", (E / "rotated_bill.pdf").read_bytes())], engine="full")).report
    assert not any("Sum insured needed" in f.title for f in r2.findings) and any(f.issue_type == "proportionate" for f in r2.findings)


GOOD = ["rotated_bill.pdf", "long_bill_60_lines.pdf", "password_protected_bill.pdf"]
BAD = ["corrupt.pdf", "blank_page.pdf", "not_a_bill.pdf", "low_res_photo.jpg", "iphone_photo.heic"]


@pytest.mark.parametrize("policy", ["demo_shield", "demo_classic", "test_no_si"])
@pytest.mark.parametrize("mode", ["pre_discharge", "pre_submission", "appeal"])
def test_edge_case_grid(policy, mode, si_less_policy):
    """Every edge-case file × every policy type × every claim stage: either a correct run or a clear user message, never a crash."""
    for name in GOOD + BAD:
        try:
            r = run(ClaimInputs(ctx=ClaimContext(mode=mode, policy_start="2021-01-01", patient_age=65), policy_id=policy,
                                bills=[(name, (E / name).read_bytes())], engine="full", password="1234")).report
            assert name in GOOD and r.claimed > 0
        except ValueError as e:
            assert name in BAD, f"{name}: {e}"
            assert len(str(e)) > 20


def test_multi_file_upload_mix():
    """Hospital PDF + rotated copy + long bill uploaded together are merged, duplicates across files are caught."""
    bills = [(n, (E / n).read_bytes()) for n in ("rotated_bill.pdf", "long_bill_60_lines.pdf")]
    bills.append(("same_again.pdf", (S / "S01_cataract_pre_discharge" / "bill.pdf").read_bytes()))
    r = run(ClaimInputs(ctx=ClaimContext(policy_start="2021-01-01", patient_age=40), policy_id="demo_shield", bills=bills, engine="full")).report
    assert any(f.issue_type == "mismatch" and "Duplicate" in f.title for f in r.findings)


def test_scanned_policy_upload_degrades_gracefully():
    r = run(ClaimInputs(ctx=ClaimContext(policy_start="2022-01-01", patient_age=40), policy_file=("p.pdf", (E / "scanned_policy.pdf").read_bytes()),
                        bills=[("b.pdf", (S / "S01_cataract_pre_discharge" / "bill.pdf").read_bytes())], engine="full")).report
    assert any(f.title == "Policy wording could not be read" for f in r.findings)
    assert any(f.issue_type == "non_payable" for f in r.findings)          # bill-level checks still work
    for f in (ROOT / "kb" / "policies").glob("upl_*.json"):
        f.unlink()
