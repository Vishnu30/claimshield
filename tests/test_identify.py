"""Content-based identification: file names and folders don't matter."""
import shutil
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from core.ingest import load
from core.doc_identify import identify, match_source

ROOT = Path(__file__).resolve().parents[1]
ROWS = [{"id": "pol_star_family_health_optima", "category": "policies", "query": "Star Family Health Optima policy wording pdf", "keywords": "optima"},
        {"id": "irdai_master_health_2024", "category": "irdai", "query": "IRDAI Master Circular", "keywords": "master|health"}]


def _pdf(tmp, name, title, body):
    ss = getSampleStyleSheet()
    p = tmp / name
    SimpleDocTemplate(str(p)).build([Paragraph(title, ss["Title"]), Paragraph(body, ss["BodyText"])])
    return load(name, p.read_bytes(), "policy")


def test_policy_identified_from_first_page(tmp_path):
    d = _pdf(tmp_path, "download (3).pdf", "Family Health Optima Insurance Plan",
             "Star Health and Allied Insurance Co. Ltd. Policy Wording UIN: SHAHLIP23131V062223. Sum Insured. Waiting period. Insured Person.")
    info = identify(d)
    assert info["kind"] == "policies" and info["insurer"] == "Star Health" and info["uin"] == "SHAHLIP23131V062223"
    assert info["product"].startswith("Family Health Optima")
    assert match_source(info, d.pages[0].text, ROWS) == "pol_star_family_health_optima"


def test_circular_identified_even_in_wrong_folder(tmp_path):
    d = _pdf(tmp_path, "x.pdf", "INSURANCE REGULATORY AND DEVELOPMENT AUTHORITY OF INDIA",
             "Ref. No: IRDAI/HLT/CIR/1/2024. Master Circular on Health Insurance Business. To all General and Health Insurers.")
    info = identify(d)
    assert info["kind"] == "irdai" and match_source(info, d.pages[0].text, ROWS) == "irdai_master_health_2024"


def test_demo_policies_identified():
    for f, ins in (("demo_shield_policy.pdf", "Demo General Insurance"), ("demo_classic_policy.pdf", "Old Shore Assurance")):
        info = identify(load(f, (ROOT / "data" / "policies" / f).read_bytes(), "policy"))
        assert info["kind"] == "policies" and info["insurer"].startswith(ins) and info["uin"]
