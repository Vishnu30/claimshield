"""Generate 2 synthetic policies + 24 claim scenarios (PDFs + ground-truth sidecars + expected.json).

python scripts/make_scenarios.py
Ground-truth ₹ figures come from core.tools.payout (never hand-typed).
"""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import pymupdf as fitz
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from core.schemas import PolicyRules, Bill, LineItem, Discharge, Rejection, RejectionReason, ClaimContext, Limit, Copay, Sublimit, WaitingPeriod, Exclusion, ClauseRef, Clause
from core import kb

SC = ROOT / "data" / "scenarios"
POL_PDF = ROOT / "data" / "policies" / "demo_shield_policy.pdf"
POL_PDF.parent.mkdir(parents=True, exist_ok=True)
ST = getSampleStyleSheet()
H = ParagraphStyle("h", parent=ST["Heading3"], spaceBefore=8, spaceAfter=4)
B = ParagraphStyle("b", parent=ST["BodyText"], fontSize=9.5, leading=13)

CLAUSES = [
    ("1.1", "Hospital", "Hospital means any institution established for in-patient care and day care treatment of illness and/or injuries and which has been registered as a hospital with the local authorities under the Clinical Establishments Act, 2010."),
    ("1.2", "Day care treatment", "Day care treatment means medical treatment and/or surgical procedure which is undertaken under general or local anaesthesia in a hospital in less than 24 hours because of technological advancement."),
    ("1.3", "Pre-existing disease", "Pre-existing disease means any condition, ailment, injury or disease that is diagnosed by a physician or for which medical advice or treatment was recommended by, or received from, a physician within 36 months prior to the date of commencement of the policy."),
    ("1.4", "Associated medical expenses", "Associated medical expenses include room rent, nursing charges, operation theatre charges, fees of the treating doctor, surgeon and anaesthetist, but do not include the cost of pharmacy and consumables, cost of implants and medical devices and cost of diagnostics."),
    ("2.1", "Sum insured", "The Sum Insured under this policy is Rs. 5,00,000 per policy year on an individual basis."),
    ("3.1", "Room rent", "Room rent, boarding and nursing expenses are payable up to 1% of the Sum Insured per day."),
    ("3.2", "Intensive care", "Intensive Care Unit charges are payable up to 2% of the Sum Insured per day."),
    ("3.3", "Proportionate deduction", "If the insured person is admitted in a room category with rent higher than the eligible limit, the associated medical expenses shall be payable in the same proportion as the eligible room rent bears to the actual room rent, except for pharmacy, consumables, implants, medical devices and diagnostics."),
    ("3.4", "Sub-limits", "Cataract surgery is payable up to Rs. 70,000 per eye per policy year."),
    ("3.5", "Co-payment", "A co-payment of 10% of the admissible claim amount applies to every claim where the insured person is aged 61 years or above at the time of admission."),
    ("4.1", "Initial waiting period", "Expenses related to the treatment of any illness within 30 days from the first policy commencement date shall be excluded except claims arising due to an accident."),
    ("4.2", "Specific waiting period", "Expenses related to the treatment of the following listed conditions shall be excluded until the expiry of 24 months of continuous coverage after the date of inception of the first policy: cataract, hernia, benign prostatic hypertrophy, joint replacement, gall bladder stones, sinusitis, piles and fissures."),
    ("4.3", "Pre-existing disease waiting period", "Expenses related to the treatment of a pre-existing disease and its direct complications shall be excluded until the expiry of 36 months of continuous coverage after the date of inception of the first policy."),
    ("4.4", "Portability", "In case of portability, the waiting periods already served under the previous policy shall be credited to the extent of the previous sum insured."),
    ("4.5", "Moratorium", "After completion of sixty continuous months of coverage, no claim under this policy shall be contestable on grounds of non-disclosure or misrepresentation, except on grounds of established fraud."),
    ("Excl05", "Cosmetic surgery", "Expenses for cosmetic or plastic surgery or any treatment to change appearance unless for reconstruction following an accident, burn or cancer are excluded."),
    ("Excl09", "Obesity treatment", "Expenses for surgical treatment of obesity that does not fulfil the stated medical criteria are excluded."),
    ("5.1", "Claim intimation", "Intimation of emergency hospitalisation must be given within 24 hours of admission. Planned hospitalisation must be intimated at least 48 hours before admission."),
    ("5.2", "Claim documents", "Reimbursement claims must be submitted within 30 days of discharge with the claim form, original itemized bills and receipts, discharge summary, investigation reports and prescriptions."),
    ("5.3", "Non-payable items", "Items listed in the Annexure of non-payable items notified by the Authority are not payable under this policy."),
    ("6.1", "Grievance", "Any grievance may be addressed to the Grievance Redressal Officer of the Company. If not resolved within 14 days, the insured may approach the Insurance Ombudsman."),
]
FILLER = ("The Company shall indemnify medical expenses incurred for hospitalisation of the insured person during the policy period, "
          "subject to the terms, conditions and exclusions of this policy, provided the hospitalisation is medically necessary and "
          "follows the written advice of a medical practitioner. ")


def render_policy(path: Path, title: str, uin: str, insurer: str, clauses: list[tuple[str, str, str]]):
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=title)
    fl = [Paragraph(f"{title} — Policy Wording (SYNTHETIC, for testing)", ST["Title"]),
          Paragraph(f"UIN: {uin} · Insurer: {insurer} (fictional)", B), Spacer(1, 8)]
    names = {"1": "Definitions", "2": "Cover", "3": "Limits", "4": "Waiting periods", "E": "Exclusions", "5": "Claims", "6": "Grievance"}
    last = None
    for cid, ttl, text in clauses:
        sec = "E" if cid.startswith("Excl") else cid.split(".")[0]
        if sec != last:
            fl.append(Paragraph(f"Section {sec}: {names.get(sec, '')}", H)); last = sec
            fl.append(Paragraph(FILLER * 4, B))
        fl.append(Paragraph(f"<b>{cid} {ttl}.</b> {text}", B)); fl.append(Spacer(1, 4))
    doc.build(fl)
    flat = [" ".join(pg.get_text().split()) for pg in fitz.open(path)]

    def page_of(q):
        qn = " ".join(q.split())[:60]
        return next((i for i, t in enumerate(flat, 1) if qn in t), None)

    def ref(cid):
        text = next(t for c, _, t in clauses if c == cid)
        return ClauseRef(clause_id=cid, page=page_of(text), quote=text)
    objs = [Clause(id=c, title=t, page=page_of(x) or 1, text=x) for c, t, x in clauses]
    return ref, objs


def build_policy() -> PolicyRules:
    ref, objs = render_policy(POL_PDF, "ClaimShield Demo Health Policy", "DEMOHLIP26001V012627", "Demo General Insurance Co. Ltd.", CLAUSES)
    return PolicyRules(
        policy_id="demo_shield", insurer="Demo General Insurance (synthetic)", product="ClaimShield Demo Health Policy",
        uin="DEMOHLIP26001V012627", jurisdiction="IN", sum_insured=500000,
        room_rent=Limit(kind="pct_si", value=1.0, cite=ref("3.1")), icu_rent=Limit(kind="pct_si", value=2.0, cite=ref("3.2")),
        proportionate_deduction=True, proportionate_cite=ref("3.3"),
        copays=[Copay(pct=10, condition="insured aged 61 or above", age_min=61, cite=ref("3.5"))],
        sublimits=[Sublimit(procedure="cataract", aliases=["phacoemulsification", "iol implantation"], limit=70000, basis="per_eye", cite=ref("3.4"))],
        waiting_periods=[WaitingPeriod(kind="initial", months=1, cite=ref("4.1")),
                         WaitingPeriod(kind="specific", months=24, conditions=["cataract", "hernia", "benign prostatic hypertrophy", "joint replacement", "gall bladder stones", "cholelithiasis", "sinusitis", "piles", "fissure"], cite=ref("4.2")),
                         WaitingPeriod(kind="ped", months=36, cite=ref("4.3"))],
        exclusions=[Exclusion(code="Excl05", text=CLAUSES[15][2], keywords=["cosmetic", "rhinoplasty", "plastic surgery"], cite=ref("Excl05")),
                    Exclusion(code="Excl09", text=CLAUSES[16][2], keywords=["bariatric", "obesity surgery"], cite=ref("Excl09"))],
        intimation_emergency_hours=24, intimation_planned_days_before=2, claim_submission_days=30,
        clauses=objs, source_file=str(POL_PDF.relative_to(ROOT)).replace("\\", "/"))


CLAUSES_B = [
    ("1.1", "Hospital", "Hospital means any institution registered with the local authorities for in-patient care with qualified nursing staff round the clock."),
    ("1.2", "Package rate", "Where the hospital charges a fixed package for a procedure, the package amount shall be considered as the total expense for that procedure."),
    ("2.1", "Sum insured", "The Sum Insured under this policy is Rs. 3,00,000 per policy year."),
    ("3.1", "Room rent", "Room, boarding and nursing expenses are payable up to Rs. 4,000 per day."),
    ("3.2", "Intensive care", "Intensive Care Unit expenses are payable up to Rs. 8,000 per day."),
    ("3.3", "Proportionate deduction", "Where the room rent exceeds the eligible limit, all other expenses except pharmacy, consumables, implants and diagnostics shall be reduced in the proportion that the eligible room rent bears to the room rent actually incurred."),
    ("3.4", "Cataract limit", "Cataract surgery is payable up to Rs. 40,000 per eye."),
    ("3.5", "Co-payment", "The insured shall bear 20% of every admissible claim if the insured person is aged 60 years or above at the time of admission."),
    ("4.1", "Initial waiting period", "Any illness contracted within 30 days of the first policy commencement is excluded, except accidental injuries."),
    ("4.2", "Specific waiting period", "The following are excluded for the first 48 months of continuous coverage: cataract, hernia, joint replacement, knee replacement, hysterectomy, gall bladder stones, kidney stones and varicose veins."),
    ("4.3", "Pre-existing diseases", "Pre-existing diseases are excluded until 48 months of continuous coverage have elapsed."),
    ("Excl03", "Cosmetic treatment", "Cosmetic, aesthetic or plastic surgery that is not medically necessary is excluded."),
    ("5.1", "Claim intimation", "The Company must be informed within 48 hours of an emergency admission."),
]
POL_B_PDF = ROOT / "data" / "policies" / "demo_classic_policy.pdf"


CLAUSES_C = [
    ("1.1", "Hospital", "Hospital means any institution registered with the local authorities for in-patient care with qualified doctors and nursing staff round the clock."),
    ("2.1", "Sum insured", "The Sum Insured under this policy is Rs. 10,00,000 per policy year on a floater basis."),
    ("3.1", "Room rent", "Room rent is payable up to the actual charges of a single private AC room."),
    ("3.2", "Intensive care", "Intensive Care Unit charges are payable at actuals."),
    ("3.3", "Proportionate deduction", "If the insured person occupies a room category higher than the eligible single private AC room, all associated medical expenses except pharmacy, consumables, implants and diagnostics shall be payable in the proportion that the tariff of the eligible room bears to the tariff of the room occupied."),
    ("3.4", "Zone co-payment", "A co-payment of 20% applies only to claims for treatment taken in a city of a higher zone than the zone of cover chosen."),
    ("4.1", "Initial waiting period", "Any illness within the first 30 days of the policy commencement is excluded, except accidents."),
    ("4.2", "Specific waiting period", "The following are excluded for the first 24 months of continuous coverage: cataract, hernia, joint replacement, gall bladder stones and kidney stones."),
    ("4.3", "Pre-existing diseases", "Pre-existing diseases are covered after 36 months of continuous coverage."),
    ("5.1", "Claim intimation", "Emergency hospitalisation must be intimated within 24 hours of admission."),
]
POL_C_PDF = ROOT / "data" / "policies" / "demo_category_policy.pdf"


def build_policy_category() -> PolicyRules:
    ref, objs = render_policy(POL_C_PDF, "Category Care Floater (Demo)", "DEMOHLIP25011V022526", "Bluebird Health Insurance Ltd.", CLAUSES_C)
    return PolicyRules(
        policy_id="demo_category", insurer="Bluebird Health Insurance (synthetic)", product="Category Care Floater (Demo)", uin="DEMOHLIP25011V022526",
        jurisdiction="IN", sum_insured=1000000, room_rent=Limit(kind="category", category="single private AC room", cite=ref("3.1")),
        icu_rent=Limit(kind="none", cite=ref("3.2")), proportionate_deduction=True, proportionate_cite=ref("3.3"),
        copays=[Copay(pct=20, condition="only for treatment in a city of a higher zone than the zone of cover", cite=ref("3.4"))],
        waiting_periods=[WaitingPeriod(kind="initial", months=1, cite=ref("4.1")),
                         WaitingPeriod(kind="specific", months=24, conditions=["cataract", "hernia", "joint replacement", "gall bladder stones", "kidney stones"], cite=ref("4.2")),
                         WaitingPeriod(kind="ped", months=36, cite=ref("4.3"))],
        intimation_emergency_hours=24, clauses=objs, source_file=str(POL_C_PDF.relative_to(ROOT)).replace("\\", "/"))


def build_policy_classic() -> PolicyRules:
    ref, objs = render_policy(POL_B_PDF, "Classic Mediclaim (Demo)", "DEMOHLIP19007V021819", "Old Shore Assurance Ltd.", CLAUSES_B)
    return PolicyRules(
        policy_id="demo_classic", insurer="Old Shore Assurance (synthetic)", product="Classic Mediclaim (Demo, legacy wording)",
        uin="DEMOHLIP19007V021819", jurisdiction="IN", sum_insured=300000,
        room_rent=Limit(kind="fixed", value=4000, cite=ref("3.1")), icu_rent=Limit(kind="fixed", value=8000, cite=ref("3.2")),
        proportionate_deduction=True, proportionate_cite=ref("3.3"),
        copays=[Copay(pct=20, condition="insured aged 60 or above", age_min=60, cite=ref("3.5"))],
        sublimits=[Sublimit(procedure="cataract", aliases=["phacoemulsification", "phaco", "sics"], limit=40000, basis="per_eye", cite=ref("3.4"))],
        waiting_periods=[WaitingPeriod(kind="initial", months=1, cite=ref("4.1")),
                         WaitingPeriod(kind="specific", months=48, conditions=["cataract", "hernia", "joint replacement", "knee replacement", "hysterectomy", "gall bladder stones", "kidney stones", "varicose veins"], cite=ref("4.2")),
                         WaitingPeriod(kind="ped", months=48, cite=ref("4.3"))],
        exclusions=[Exclusion(code="Excl03", text=CLAUSES_B[11][2], keywords=["cosmetic", "aesthetic", "rhinoplasty"], cite=ref("Excl03"))],
        intimation_emergency_hours=48, clauses=objs, source_file=str(POL_B_PDF.relative_to(ROOT)).replace("\\", "/"))


# ----------------------------------------------------------------- documents
def bill_pdf(path: Path, bill: Bill, patient: dict):
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Final Bill")
    rows = [["#", "Date", "Description", "Qty", "Rate", "Amount (Rs.)"]]
    for n, it in enumerate(bill.line_items, 1):
        rows.append([str(n), it.date or "", it.description, f"{it.qty:g}", f"{it.rate:,.0f}" if it.rate else "", f"{it.amount:,.0f}"])
    if bill.discount:
        rows.append(["", "", "Less: discount", "", "", f"-{bill.discount:,.0f}"])
    rows.append(["", "", "Grand Total", "", "", f"{bill.stated_total:,.0f}"])
    t = Table(rows, colWidths=[22, 62, 240, 34, 56, 80])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                           ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("ALIGN", (3, 1), (-1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    fl = [Paragraph(f"{bill.hospital}", ST["Title"]), Paragraph("FINAL IN-PATIENT BILL", ST["Heading3"]),
          Paragraph(f"Patient Name: {patient['name']}<br/>UHID: {patient['uhid']} · Mobile: {patient['phone']}<br/>"
                    f"Bill No: {bill.bill_no} · Admission: {bill.admission_date} · Discharge: {bill.discharge_date}<br/>"
                    f"Room category: {bill.room_category}", B), Spacer(1, 8), t]
    doc.build(fl)


def text_pdf(path: Path, title: str, lines: list[str]):
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=title)
    fl = [Paragraph(title, ST["Title"])] + [Paragraph(l, B) for l in lines]
    doc.build(fl)


def L(i, desc, cat, amt, qty=1, rate=None, date="2026-10-20", page=1):
    return LineItem(id=i, description=desc, category=cat, qty=qty, rate=rate if rate is not None else (amt / qty if qty else amt), amount=amt, date=date, page=page)


ALL_DOCS = ["claim_form_part_a", "claim_form_part_b", "final_itemized_bill", "payment_receipts", "discharge_summary", "investigation_reports",
            "prescriptions", "pharmacy_bills", "id_proof", "policy_copy", "cancelled_cheque"]


def scenarios():
    S = []
    # S01 — hero demo: cataract, room-rent breach, PD, non-payables, kit, total mismatch, senior copay
    items = [L("R1", "Deluxe room (1 day)", "room", 7500, 1, 7500), L("N1", "Nursing charges", "nursing", 1200),
             L("S1", "Surgeon fee - phacoemulsification", "surgeon", 18000), L("A1", "Anaesthetist fee", "anaesthesia", 4500),
             L("O1", "OT charges", "ot", 9000), L("I1", "Foldable IOL (implant)", "implant", 16500), L("K1", "Phaco kit", "consumable", 3800),
             L("P1", "Pharmacy & drugs", "pharmacy", 3400), L("V1", "Investigations (biometry, A-scan, blood tests)", "investigation", 2600),
             L("X1", "Attendant food", "misc", 900), L("X2", "Registration & documentation charges", "misc", 500),
             L("X3", "Gloves (examination)", "consumable", 450), L("X4", "Mask & shoe cover", "consumable", 300),
             L("X5", "Admission kit", "misc", 650), L("X6", "Eye drape", "consumable", 350)]
    S.append(dict(id="S01_cataract_pre_discharge", title="Cataract — pre-discharge audit (hero demo)",
                  bill=Bill(hospital="Sunrise Eye & General Hospital, Ahmedabad", bill_no="SEH/26/10412", admission_date="2026-10-20", discharge_date="2026-10-21",
                            room_category="Deluxe single", room_days=1, line_items=items, stated_total=71350),
                  discharge=Discharge(diagnosis="Senile mature cataract, right eye", procedure="Phacoemulsification with foldable IOL implantation, right eye",
                                      icd10="H25.1", admission_type="day_care", comorbidities=["hypertension"], history_notes="Known hypertensive for 4 years on amlodipine.", condition_duration_months=48),
                  ctx=ClaimContext(mode="pre_discharge", patient_age=63, policy_start="2023-04-01", ped_declared=["hypertension"],
                                   docs_provided=["final_itemized_bill", "discharge_summary", "prescriptions", "payment_receipts", "investigation_reports"],
                                   narrative="Mere papa ka cataract operation hua hai, kal discharge hai. Room deluxe liya tha kyunki general full tha."),
                  patient=dict(name="Ramesh Patel", uhid="SEH-884213", phone="9876543210"),
                  expected=[("mismatch", "fixable"), ("non_payable", "valid"), ("non_payable", "fixable"), ("room_rent", "valid"),
                            ("proportionate", "fixable"), ("copay", "valid"), ("missing_doc", "fixable")]))
    # S02 — hernia inside specific waiting period (valid rejection)
    S.append(dict(id="S02_hernia_waiting_valid", title="Hernia — inside 24-month waiting period",
                  bill=Bill(hospital="City Care Hospital, Pune", bill_no="CCH-7781", admission_date="2026-10-05", discharge_date="2026-10-07", room_category="Twin sharing", room_days=2,
                            line_items=[L("R1", "Twin sharing room", "room", 6000, 2, 3000, "2026-10-05"), L("S1", "Surgeon fee - laparoscopic hernioplasty", "surgeon", 35000, date="2026-10-05"),
                                        L("A1", "Anaesthesia", "anaesthesia", 8000, date="2026-10-05"), L("O1", "OT charges", "ot", 15000, date="2026-10-05"),
                                        L("I1", "Prolene mesh (implant)", "implant", 12000, date="2026-10-05"), L("P1", "Pharmacy", "pharmacy", 6500, date="2026-10-06"),
                                        L("V1", "Pre-op investigations", "investigation", 4200, date="2026-10-05")], stated_total=86700),
                  discharge=Discharge(diagnosis="Right inguinal hernia", procedure="Laparoscopic TEP hernioplasty with mesh", icd10="K40.9", admission_type="planned"),
                  ctx=ClaimContext(mode="pre_submission", patient_age=40, policy_start="2025-08-01", docs_provided=ALL_DOCS + ["implant_invoice_sticker"]),
                  patient=dict(name="Anita Deshmukh", uhid="CCH-11902", phone="9822012345"),
                  expected=[("waiting_period", "valid")]))
    # S03 — emergency appendicectomy: missing documents + late intimation
    S.append(dict(id="S03_missing_docs_late_intimation", title="Appendicitis — missing documents + late intimation",
                  bill=Bill(hospital="Lakeview Hospital, Bengaluru", bill_no="LVH-30021", admission_date="2026-09-14", discharge_date="2026-09-17", room_category="General ward", room_days=3,
                            line_items=[L("R1", "General ward", "room", 4500, 3, 1500, "2026-09-14"), L("S1", "Surgeon fee - laparoscopic appendicectomy", "surgeon", 30000, date="2026-09-14"),
                                        L("A1", "Anaesthesia", "anaesthesia", 7000, date="2026-09-14"), L("O1", "OT charges", "ot", 12000, date="2026-09-14"),
                                        L("P1", "Pharmacy", "pharmacy", 9800, date="2026-09-15"), L("V1", "CT abdomen & labs", "investigation", 8700, date="2026-09-14")], stated_total=72000),
                  discharge=Discharge(diagnosis="Acute appendicitis", procedure="Laparoscopic appendicectomy", icd10="K35.8", admission_type="emergency"),
                  ctx=ClaimContext(mode="pre_submission", patient_age=35, policy_start="2022-01-10", docs_provided=["final_itemized_bill", "payment_receipts", "claim_form_part_a"],
                                   intimation_hours_after_admission=40),
                  patient=dict(name="Kiran Rao", uhid="LVH-55120", phone="9900112233"),
                  expected=[("missing_doc", "fixable"), ("intimation", "contestable")]))
    # S04 — cataract above sub-limit (valid)
    S.append(dict(id="S04_cataract_sublimit", title="Premium IOL cataract — sub-limit exceeded",
                  bill=Bill(hospital="Vision Plus Eye Centre, Mumbai", bill_no="VP-2210", admission_date="2026-08-11", discharge_date="2026-08-11", room_category="Day care", room_days=1,
                            line_items=[L("R1", "Day care bed", "room", 3000, date="2026-08-11"), L("S1", "Surgeon fee - femto phaco", "surgeon", 38000, date="2026-08-11"),
                                        L("O1", "OT charges", "ot", 12000, date="2026-08-11"), L("I1", "Trifocal IOL (implant)", "implant", 36000, date="2026-08-11"),
                                        L("P1", "Pharmacy", "pharmacy", 2000, date="2026-08-11"), L("V1", "Biometry & OCT", "investigation", 3000, date="2026-08-11")], stated_total=94000),
                  discharge=Discharge(diagnosis="Immature cataract, left eye", procedure="Femto-laser assisted phacoemulsification with trifocal IOL", admission_type="day_care"),
                  ctx=ClaimContext(mode="pre_submission", patient_age=52, policy_start="2019-06-01", docs_provided=ALL_DOCS + ["implant_invoice_sticker"]),
                  patient=dict(name="Farah Shaikh", uhid="VP-90021", phone="9819988776"),
                  expected=[("sublimit", "valid")]))
    # S05 — clean control
    S.append(dict(id="S05_clean_dengue", title="Dengue — clean claim (control)",
                  bill=Bill(hospital="Metro Hospital, Jaipur", bill_no="MH-4410", admission_date="2026-09-02", discharge_date="2026-09-05", room_category="Single private", room_days=3,
                            line_items=[L("R1", "Single private room", "room", 13500, 3, 4500, "2026-09-02"), L("C1", "Physician visits", "consultation", 6000, date="2026-09-02"),
                                        L("N1", "Nursing charges", "nursing", 3000, date="2026-09-02"), L("P1", "Pharmacy & IV fluids", "pharmacy", 14500, date="2026-09-03"),
                                        L("V1", "CBC, NS1, platelet monitoring", "investigation", 11000, date="2026-09-02")], stated_total=48000),
                  discharge=Discharge(diagnosis="Dengue fever with thrombocytopenia", procedure="Conservative management", icd10="A90", admission_type="emergency"),
                  ctx=ClaimContext(mode="pre_submission", patient_age=30, policy_start="2021-03-15", docs_provided=ALL_DOCS, intimation_hours_after_admission=6),
                  patient=dict(name="Vikram Singh", uhid="MH-3321", phone="9414011223"),
                  expected=[]))
    # S06 — hernia with portability credit (fixable)
    S.append(dict(id="S06_hernia_portability", title="Hernia — waiting period served via portability",
                  bill=Bill(hospital="City Care Hospital, Pune", bill_no="CCH-7790", admission_date="2026-10-08", discharge_date="2026-10-10", room_category="Twin sharing", room_days=2,
                            line_items=[L("R1", "Twin sharing room", "room", 6000, 2, 3000, "2026-10-08"), L("S1", "Surgeon fee - open hernioplasty", "surgeon", 28000, date="2026-10-08"),
                                        L("A1", "Anaesthesia", "anaesthesia", 7000, date="2026-10-08"), L("O1", "OT charges", "ot", 12000, date="2026-10-08"),
                                        L("I1", "Polypropylene mesh (implant)", "implant", 9000, date="2026-10-08"), L("P1", "Pharmacy", "pharmacy", 5000, date="2026-10-09"),
                                        L("V1", "Pre-op investigations", "investigation", 3500, date="2026-10-08")], stated_total=70500),
                  discharge=Discharge(diagnosis="Left inguinal hernia", procedure="Lichtenstein open hernioplasty with mesh", admission_type="planned"),
                  ctx=ClaimContext(mode="pre_submission", patient_age=46, policy_start="2025-08-01", continuous_coverage_since="2022-05-01",
                                   docs_provided=ALL_DOCS + ["implant_invoice_sticker"], narrative="I ported from my old insurer in August 2025; I have had cover since 2022."),
                  patient=dict(name="Suresh Kulkarni", uhid="CCH-11950", phone="9823098230"),
                  expected=[("waiting_period", "fixable"), ("missing_doc", "fixable")]))
    # S07 — billing errors (duplicate + qty×rate)
    S.append(dict(id="S07_billing_errors", title="Pneumonia — duplicate & arithmetic billing errors",
                  bill=Bill(hospital="Green Valley Hospital, Indore", bill_no="GVH-1209", admission_date="2026-09-20", discharge_date="2026-09-24", room_category="Twin sharing", room_days=4,
                            line_items=[L("R1", "Twin sharing room", "room", 12000, 4, 3000, "2026-09-20"), L("C1", "Physician visits", "consultation", 8000, date="2026-09-20"),
                                        L("P1", "Pharmacy - antibiotics", "pharmacy", 9600, date="2026-09-21"), L("P2", "Pharmacy - antibiotics", "pharmacy", 9600, date="2026-09-21"),
                                        L("N1", "Nebulisation (5 sessions)", "procedure", 2000, 5, 300, "2026-09-21"), L("V1", "Chest X-ray & labs", "investigation", 6800, date="2026-09-20")],
                            stated_total=48000),
                  discharge=Discharge(diagnosis="Community acquired pneumonia", procedure="IV antibiotics, nebulisation", admission_type="emergency"),
                  ctx=ClaimContext(mode="pre_submission", patient_age=45, policy_start="2020-02-01", docs_provided=ALL_DOCS, intimation_hours_after_admission=10),
                  patient=dict(name="Meena Joshi", uhid="GVH-7781", phone="9425012345"),
                  expected=[("mismatch", "fixable"), ("mismatch", "fixable")]))
    # S08 — wrongful PED rejection after moratorium (appeal)
    S.append(dict(id="S08_ped_rejection_appeal", title="Angioplasty — PED rejection after moratorium (appeal)",
                  bill=Bill(hospital="Heartline Institute, Delhi", bill_no="HLI-88123", admission_date="2026-09-10", discharge_date="2026-09-14", room_category="Single private", room_days=4, icu_days=2,
                            line_items=[L("R1", "Single private room", "room", 18000, 4, 4500, "2026-09-10"), L("U1", "ICU", "icu", 18000, 2, 9000, "2026-09-10"),
                                        L("S1", "Cardiologist - PTCA", "surgeon", 55000, date="2026-09-11"), L("O1", "Cath lab charges", "ot", 60000, date="2026-09-11"),
                                        L("I1", "Drug eluting stent (implant)", "implant", 38000, date="2026-09-11"), L("P1", "Pharmacy", "pharmacy", 36000, date="2026-09-12"),
                                        L("V1", "Angiography, echo, labs", "investigation", 25000, date="2026-09-10"), L("N1", "Nursing charges", "nursing", 6000, date="2026-09-10")],
                            stated_total=256000),
                  discharge=Discharge(diagnosis="Acute anterior wall myocardial infarction", procedure="Primary PTCA with DES to LAD", icd10="I21.0", admission_type="emergency",
                                      comorbidities=["hypertension"], history_notes="Hypertensive for 3 years, irregular medication."),
                  rejection=Rejection(insurer="Demo General Insurance (synthetic)", letter_date="2026-10-09", claim_no="DGI/HL/26/55102", decision="rejected", amount_claimed=256000, amount_paid=0,
                                      reasons=[RejectionReason(text="Claim repudiated due to non-disclosure of pre-existing hypertension at the time of proposal", clause_cited="4.3")]),
                  ctx=ClaimContext(mode="appeal", patient_age=58, policy_start="2020-04-01", docs_provided=ALL_DOCS + ["rejection_letter", "implant_invoice_sticker"], intimation_hours_after_admission=8),
                  patient=dict(name="Harish Malhotra", uhid="HLI-20019", phone="9811122233"),
                  expected=[("wrongful_rejection", "contestable"), ("missing_doc", "fixable")]))
    return S


# ----------------------------------------------------------------- noisy renders (vision / OCR robustness)
def degrade(png: bytes, angle: float, seed: int, dark: float = 0.92):
    import io, random
    from PIL import Image, ImageFilter, ImageEnhance
    im = Image.open(io.BytesIO(png)).convert("L")
    im = im.rotate(angle, expand=True, fillcolor=235).filter(ImageFilter.GaussianBlur(0.6))
    rnd = random.Random(seed)
    px = im.load()
    for _ in range(im.size[0] * im.size[1] // 40):
        x, y = rnd.randrange(im.size[0]), rnd.randrange(im.size[1])
        px[x, y] = max(0, px[x, y] - rnd.randrange(40, 120))
    return ImageEnhance.Brightness(im).enhance(dark)


def to_scan_pdf(pdf: Path, angle=1.4, seed=7):
    import io
    png = fitz.open(pdf)[0].get_pixmap(dpi=110).tobytes("png")
    im = degrade(png, angle, seed)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=55)
    out = fitz.open(); pg = out.new_page(width=595, height=842)
    pg.insert_image(pg.rect, stream=buf.getvalue())
    out.save(pdf)


def to_photo(pdf: Path, jpg: Path, angle=-3.2, seed=11):
    png = fitz.open(pdf)[0].get_pixmap(dpi=100).tobytes("png")
    degrade(png, angle, seed, dark=0.8).save(jpg, "JPEG", quality=50)
    pdf.unlink()


def scenarios_extra():
    A, Bp = "demo_shield", "demo_classic"
    S = []
    S.append(dict(id="S09_icu_cap_breach", title="Dengue shock — ICU above daily limit", policy=A,
        bill=Bill(hospital="Apex Multispeciality, Hyderabad", bill_no="APX-5512", admission_date="2026-09-15", discharge_date="2026-09-20", room_category="Single private", room_days=2, icu_days=3,
                  line_items=[L("R1", "Single private room", "room", 9000, 2, 4500, "2026-09-18"), L("U1", "ICU charges", "icu", 42000, 3, 14000, "2026-09-15"),
                              L("C1", "Physician visits", "consultation", 9000, date="2026-09-15"), L("N1", "Nursing charges", "nursing", 4000, date="2026-09-15"),
                              L("P1", "Pharmacy & IV fluids", "pharmacy", 26000, date="2026-09-16"), L("V1", "Platelet monitoring & labs", "investigation", 15000, date="2026-09-15")], stated_total=105000),
        discharge=Discharge(diagnosis="Dengue shock syndrome", procedure="ICU management, platelet transfusion", icd10="A91", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=34, policy_start="2020-01-01", docs_provided=ALL_DOCS, intimation_hours_after_admission=5),
        patient=dict(name="Sravani Reddy", uhid="APX-77120", phone="9849012345"), expected=[("icu_rent", "valid")]))
    S.append(dict(id="S10_package_cataract", title="Cataract package bill — legacy policy sub-limit", policy=Bp,
        bill=Bill(hospital="Netra Jyoti Eye Hospital, Lucknow", bill_no="NJ-3301", admission_date="2026-08-05", discharge_date="2026-08-05", package=True,
                  line_items=[L("K1", "Cataract surgery package (phaco + monofocal IOL)", "package", 55000, date="2026-08-05"),
                              L("V1", "Pre-operative investigations", "investigation", 2500, date="2026-08-04")], stated_total=57500),
        discharge=Discharge(diagnosis="Senile cataract, left eye", procedure="Phacoemulsification with IOL (package)", admission_type="day_care"),
        ctx=ClaimContext(mode="pre_submission", patient_age=55, policy_start="2019-04-01", docs_provided=ALL_DOCS + ["implant_invoice_sticker"]),
        patient=dict(name="Ram Prakash", uhid="NJ-1180", phone="9415098765"), expected=[("sublimit", "valid")]))
    S.append(dict(id="S11_scanned_cataract_gujarati", title="Scanned bill (rotated, noisy) — cataract, Gujarati output", policy=A, render="scan",
        bill=Bill(hospital="Shreeji Eye Hospital, Surat", bill_no="SEH-2291", admission_date="2026-10-02", discharge_date="2026-10-03", room_category="Deluxe", room_days=1,
                  line_items=[L("R1", "Deluxe room (1 day)", "room", 8000, 1, 8000, "2026-10-02"), L("N1", "Nursing charges", "nursing", 1000, date="2026-10-02"),
                              L("S1", "Surgeon fee - phaco", "surgeon", 16000, date="2026-10-02"), L("A1", "Anaesthetist fee", "anaesthesia", 4000, date="2026-10-02"),
                              L("O1", "OT charges", "ot", 8000, date="2026-10-02"), L("I1", "Hydrophobic IOL (implant)", "implant", 14000, date="2026-10-02"),
                              L("K1", "Surgical kit", "consumable", 3000, date="2026-10-02"), L("P1", "Pharmacy", "pharmacy", 2800, date="2026-10-02"),
                              L("V1", "Investigations", "investigation", 2200, date="2026-10-01"), L("X1", "Food charges (attendant)", "misc", 600, date="2026-10-02"),
                              L("X2", "Toiletries kit", "misc", 400, date="2026-10-02")], stated_total=61200),
        discharge=Discharge(diagnosis="Mature cataract, right eye", procedure="Phacoemulsification with IOL, right eye", admission_type="day_care"),
        ctx=ClaimContext(mode="pre_discharge", patient_age=66, policy_start="2021-11-01", user_lang="gu", docs_provided=["final_itemized_bill", "discharge_summary", "investigation_reports"],
                         narrative="મારી માતાનું મોતિયાનું ઓપરેશન થયું છે. હોસ્પિટલે ડીલક્સ રૂમ આપ્યો હતો અને બિલ બહુ વધારે લાગે છે."),
        voice=("guj", "મારી માતાનું મોતિયાનું ઓપરેશન થયું છે. હોસ્પિટલે ડીલક્સ રૂમ આપ્યો હતો અને બિલ બહુ વધારે લાગે છે."),
        patient=dict(name="Hansaben Shah", uhid="SEH-5501", phone="9825011223"),
        expected=[("mismatch", "fixable"), ("non_payable", "valid"), ("non_payable", "fixable"), ("room_rent", "valid"), ("proportionate", "fixable"), ("copay", "valid"), ("missing_doc", "fixable")]))
    S.append(dict(id="S12_phone_photo_gastro", title="Phone photo of bill (JPG) — kit & non-payables", policy=A, render="photo",
        bill=Bill(hospital="Care Point Hospital, Nagpur", bill_no="CPH-118", admission_date="2026-09-08", discharge_date="2026-09-10", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 6000, 2, 3000, "2026-09-08"), L("C1", "Physician visits", "consultation", 3000, date="2026-09-08"),
                              L("P1", "Pharmacy & IV fluids", "pharmacy", 7500, date="2026-09-08"), L("V1", "Stool & blood tests", "investigation", 3500, date="2026-09-08"),
                              L("K1", "Dressing kit", "consumable", 900, date="2026-09-09"), L("X1", "Gloves", "consumable", 300, date="2026-09-09"),
                              L("X2", "Thermometer", "misc", 250, date="2026-09-09")], stated_total=21450),
        discharge=Discharge(diagnosis="Acute gastroenteritis with dehydration", procedure="IV fluids", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=29, policy_start="2022-02-01", docs_provided=ALL_DOCS, intimation_hours_after_admission=4),
        patient=dict(name="Rohit Wankhede", uhid="CPH-9001", phone="9822112233"), expected=[("non_payable", "valid"), ("non_payable", "fixable"), ("missing_doc", "fixable")]))
    main = [L("R1", "Twin sharing room", "room", 10500, 3, 3500, "2026-09-03"), L("S1", "Surgeon fee - lap cholecystectomy", "surgeon", 32000, date="2026-09-03"),
            L("A1", "Anaesthesia", "anaesthesia", 8000, date="2026-09-03"), L("O1", "OT charges", "ot", 14000, date="2026-09-03"),
            L("V1", "Ultrasound & LFT", "investigation", 6000, date="2026-09-02"), L("P1", "Pharmacy - in-patient medicines", "pharmacy", 7400, date="2026-09-04")]
    pharm = [L("F1", "Pantoprazole injection", "pharmacy", 1200, date="2026-09-04"), L("F2", "Ceftriaxone injection", "pharmacy", 2400, date="2026-09-04"),
             L("F3", "Pharmacy - in-patient medicines", "pharmacy", 7400, date="2026-09-04")]
    S.append(dict(id="S13_multi_bill_duplicate_bengali", title="Two bills (hospital + pharmacy) — duplicated medicines, Bengali output", policy=A,
        bill=Bill(hospital="Ruby Park Hospital, Kolkata", bill_no="RPH-7720", admission_date="2026-09-02", discharge_date="2026-09-05", room_category="Twin sharing", room_days=3,
                  line_items=main + pharm, stated_total=88900),
        parts=[("bill.pdf", main, 77900, "Ruby Park Hospital, Kolkata"), ("bill_pharmacy.pdf", pharm, 11000, "Ruby Park Hospital — In-house Pharmacy")],
        discharge=Discharge(diagnosis="Symptomatic cholelithiasis", procedure="Laparoscopic cholecystectomy", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", patient_age=48, policy_start="2021-06-01", user_lang="bn", docs_provided=ALL_DOCS,
                         narrative="হাসপাতালের ফার্মেসি বিল আলাদা দিয়েছে, কিছু ওষুধ দুবার লেখা আছে মনে হচ্ছে।"),
        voice=("ben", "হাসপাতালের ফার্মেসি বিল আলাদা দিয়েছে, কিছু ওষুধ দুবার লেখা আছে মনে হচ্ছে।"),
        patient=dict(name="Sudipta Banerjee", uhid="RPH-4410", phone="9830012345"), expected=[("mismatch", "fixable")]))
    S.append(dict(id="S14_pd_no_differential_kannada", title="Room upgrade, hospital bills flat fees — PD contestable, Kannada output", policy=A,
        bill=Bill(hospital="Sparsh Ortho Centre, Bengaluru", bill_no="SOC-6610", admission_date="2026-09-22", discharge_date="2026-09-24", room_category="Single deluxe", room_days=2,
                  differential_billing=False,
                  line_items=[L("R1", "Single deluxe room", "room", 14000, 2, 7000, "2026-09-22"), L("S1", "Surgeon fee - arthroscopic ACL reconstruction", "surgeon", 45000, date="2026-09-22"),
                              L("A1", "Anaesthesia", "anaesthesia", 10000, date="2026-09-22"), L("O1", "OT charges", "ot", 20000, date="2026-09-22"),
                              L("I1", "ACL graft fixation screws (implant)", "implant", 28000, date="2026-09-22"), L("P1", "Pharmacy", "pharmacy", 8000, date="2026-09-23"),
                              L("V1", "MRI knee", "investigation", 9000, date="2026-09-21"), L("N1", "Nursing charges", "nursing", 3000, date="2026-09-22")], stated_total=137000),
        discharge=Discharge(diagnosis="Complete ACL tear, right knee", procedure="Arthroscopic ACL reconstruction", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", patient_age=50, policy_start="2018-05-01", user_lang="kn", docs_provided=ALL_DOCS + ["implant_invoice_sticker"],
                         narrative="ನನ್ನ ಮೊಣಕಾಲಿಗೆ ಆರ್ಥ್ರೋಸ್ಕೋಪಿ ಆಯಿತು. ಎಲ್ಲಾ ಕೊಠಡಿಗಳಿಗೂ ಒಂದೇ ಶುಲ್ಕ ಎಂದು ಆಸ್ಪತ್ರೆ ಬರೆದು ಕೊಟ್ಟಿದೆ."),
        voice=("kan", "ನನ್ನ ಮೊಣಕಾಲಿಗೆ ಆರ್ಥ್ರೋಸ್ಕೋಪಿ ಆಯಿತು. ಎಲ್ಲಾ ಕೊಠಡಿಗಳಿಗೂ ಒಂದೇ ಶುಲ್ಕ ಎಂದು ಆಸ್ಪತ್ರೆ ಬರೆದು ಕೊಟ್ಟಿದೆ."),
        patient=dict(name="Manjunath Gowda", uhid="SOC-2210", phone="9845098450"), expected=[("room_rent", "valid"), ("proportionate", "contestable")]))
    S.append(dict(id="S15_waiting_regulator_cap_marathi", title="Knee replacement at 40 months — 48-month clause capped by regulator, Marathi output", policy=Bp,
        bill=Bill(hospital="Sahyadri Joint Care, Pune", bill_no="SJC-9001", admission_date="2026-10-12", discharge_date="2026-10-16", room_category="Twin sharing", room_days=4,
                  line_items=[L("R1", "Twin sharing room", "room", 14000, 4, 3500, "2026-10-12"), L("S1", "Surgeon fee - total knee replacement", "surgeon", 60000, date="2026-10-12"),
                              L("A1", "Anaesthesia", "anaesthesia", 15000, date="2026-10-12"), L("O1", "OT charges", "ot", 25000, date="2026-10-12"),
                              L("I1", "Knee prosthesis (implant)", "implant", 90000, date="2026-10-12"), L("P1", "Pharmacy", "pharmacy", 18000, date="2026-10-13"),
                              L("V1", "Investigations", "investigation", 12000, date="2026-10-11"), L("T1", "Physiotherapy sessions", "procedure", 6000, date="2026-10-14")], stated_total=240000),
        discharge=Discharge(diagnosis="Primary osteoarthritis, right knee", procedure="Total knee replacement, right", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", patient_age=66, policy_start="2023-06-01", user_lang="mr", docs_provided=ALL_DOCS + ["implant_invoice_sticker"],
                         narrative="माझ्या वडिलांचे गुडघा बदलण्याचे ऑपरेशन झाले. पॉलिसी तीन वर्षांपेक्षा जास्त जुनी आहे."),
        voice=("mar", "माझ्या वडिलांचे गुडघा बदलण्याचे ऑपरेशन झाले. पॉलिसी तीन वर्षांपेक्षा जास्त जुनी आहे."),
        patient=dict(name="Vasant Jadhav", uhid="SJC-3301", phone="9890012345"), expected=[("waiting_period", "contestable"), ("ped", "needs_review")]))
    S.append(dict(id="S16_initial_waiting_valid", title="Typhoid in first 30 days — initial waiting (valid)", policy=A,
        bill=Bill(hospital="Shanti Hospital, Patna", bill_no="SH-221", admission_date="2026-09-19", discharge_date="2026-09-22", room_category="Twin sharing", room_days=3,
                  line_items=[L("R1", "Twin sharing room", "room", 9000, 3, 3000, "2026-09-19"), L("C1", "Physician visits", "consultation", 4500, date="2026-09-19"),
                              L("P1", "Pharmacy", "pharmacy", 8000, date="2026-09-20"), L("V1", "Widal, blood culture", "investigation", 5500, date="2026-09-19")], stated_total=27000),
        discharge=Discharge(diagnosis="Enteric fever (typhoid)", procedure="IV antibiotics", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=27, policy_start="2026-09-01", docs_provided=ALL_DOCS, intimation_hours_after_admission=3),
        patient=dict(name="Ankit Kumar", uhid="SH-1012", phone="9431012345"), expected=[("waiting_period", "valid")]))
    S.append(dict(id="S17_accident_within_30_days", title="Road accident on day 10 — waiting waived, FIR/MLC needed", policy=A,
        bill=Bill(hospital="Trauma Care Centre, Chandigarh", bill_no="TCC-441", admission_date="2026-10-05", discharge_date="2026-10-07", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 6000, 2, 3000, "2026-10-05"), L("S1", "Orthopaedic surgeon fee - ORIF", "surgeon", 25000, date="2026-10-05"),
                              L("A1", "Anaesthesia", "anaesthesia", 6000, date="2026-10-05"), L("O1", "OT charges", "ot", 12000, date="2026-10-05"),
                              L("I1", "Locking plate & screws (implant)", "implant", 18000, date="2026-10-05"), L("P1", "Pharmacy", "pharmacy", 5000, date="2026-10-06"),
                              L("V1", "X-ray & CT", "investigation", 2500, date="2026-10-05")], stated_total=74500),
        discharge=Discharge(diagnosis="Fracture distal radius, left, following road traffic accident", procedure="ORIF with locking plate", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=31, policy_start="2026-09-25", docs_provided=ALL_DOCS + ["implant_invoice_sticker"], intimation_hours_after_admission=6),
        patient=dict(name="Gurpreet Kaur", uhid="TCC-7781", phone="9876012345"), expected=[("missing_doc", "fixable")]))
    S.append(dict(id="S18_cosmetic_exclusion", title="Rhinoplasty — possible cosmetic exclusion", policy=A,
        bill=Bill(hospital="Aesthetica Clinic, Mumbai", bill_no="AC-90", admission_date="2026-08-20", discharge_date="2026-08-20", room_category="Day care", room_days=1,
                  line_items=[L("R1", "Day care room", "room", 3000, date="2026-08-20"), L("S1", "Surgeon fee - rhinoplasty", "surgeon", 60000, date="2026-08-20"),
                              L("O1", "OT charges", "ot", 15000, date="2026-08-20"), L("A1", "Anaesthesia", "anaesthesia", 8000, date="2026-08-20"),
                              L("P1", "Pharmacy", "pharmacy", 4000, date="2026-08-20")], stated_total=90000),
        discharge=Discharge(diagnosis="Nasal hump deformity", procedure="Cosmetic rhinoplasty", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", patient_age=26, policy_start="2020-07-01", docs_provided=ALL_DOCS),
        patient=dict(name="Neha Kapoor", uhid="AC-551", phone="9820098200"), expected=[("exclusion", "needs_review")]))
    S.append(dict(id="S19_undisclosed_ped_tamil", title="Diabetic foot — undeclared diabetes, Tamil output", policy=A,
        bill=Bill(hospital="Kauvery Hospital, Chennai", bill_no="KH-3399", admission_date="2026-10-10", discharge_date="2026-10-15", room_category="Twin sharing", room_days=5,
                  line_items=[L("R1", "Twin sharing room", "room", 15000, 5, 3000, "2026-10-10"), L("S1", "Surgeon fee - debridement", "surgeon", 15000, date="2026-10-11"),
                              L("O1", "OT charges", "ot", 8000, date="2026-10-11"), L("P1", "Pharmacy & antibiotics", "pharmacy", 22000, date="2026-10-11"),
                              L("V1", "Cultures, HbA1c, X-ray", "investigation", 7000, date="2026-10-10"), L("T1", "Dressing charges", "procedure", 4000, date="2026-10-12")], stated_total=71000),
        discharge=Discharge(diagnosis="Diabetic foot ulcer with cellulitis, left foot", procedure="Wound debridement", admission_type="emergency", comorbidities=["diabetes"],
                            history_notes="Type 2 diabetes mellitus for 6 years on metformin.", condition_duration_months=72),
        ctx=ClaimContext(mode="pre_submission", patient_age=57, policy_start="2024-08-01", user_lang="ta", docs_provided=ALL_DOCS, intimation_hours_after_admission=8,
                         narrative="என் அம்மாவுக்கு சர்க்கரை நோய் காயத்துக்கு சிகிச்சை நடந்தது. பாலிசி இரண்டு வருடம் முன்பு எடுத்தோம்."),
        voice=("tam", "என் அம்மாவுக்கு சர்க்கரை நோய் காயத்துக்கு சிகிச்சை நடந்தது. பாலிசி இரண்டு வருடம் முன்பு எடுத்தோம்."),
        patient=dict(name="Lakshmi Sundaram", uhid="KH-8812", phone="9840012345"), expected=[("ped", "needs_review")]))
    S.append(dict(id="S20_short_settlement_pd_telugu", title="Short-settlement — PD applied to medicines & implants (appeal), Telugu output", policy=A, insurer_pd_on_all=True,
        bill=Bill(hospital="Yashoda Hospital, Secunderabad", bill_no="YH-12011", admission_date="2026-08-28", discharge_date="2026-08-31", room_category="Single private", room_days=3,
                  line_items=[L("R1", "Single private room", "room", 19500, 3, 6500, "2026-08-28"), L("S1", "Surgeon fee - arthroscopic meniscectomy", "surgeon", 40000, date="2026-08-28"),
                              L("A1", "Anaesthesia", "anaesthesia", 9000, date="2026-08-28"), L("O1", "OT charges", "ot", 18000, date="2026-08-28"),
                              L("N1", "Nursing charges", "nursing", 3000, date="2026-08-28"), L("I1", "Bioabsorbable screws (implant)", "implant", 22000, date="2026-08-28"),
                              L("P1", "Pharmacy", "pharmacy", 14000, date="2026-08-29"), L("V1", "MRI & labs", "investigation", 10000, date="2026-08-27")], stated_total=135500),
        discharge=Discharge(diagnosis="Medial meniscus tear, right knee", procedure="Arthroscopic partial meniscectomy", admission_type="planned"),
        ctx=ClaimContext(mode="appeal", patient_age=45, policy_start="2019-03-01", user_lang="te", docs_provided=ALL_DOCS + ["rejection_letter", "implant_invoice_sticker"],
                         narrative="బీమా సంస్థ మా క్లెయిమ్‌లో చాలా మొత్తం తగ్గించింది. గది అద్దె ఎక్కువ అని చెప్పారు."),
        voice=("tel", "బీమా సంస్థ మా క్లెయిమ్‌లో చాలా మొత్తం తగ్గించింది. గది అద్దె ఎక్కువ అని చెప్పారు."),
        patient=dict(name="Srinivas Rao", uhid="YH-5521", phone="9848012345"),
        expected=[("room_rent", "valid"), ("proportionate", "contestable"), ("short_settlement", "contestable")]))
    S.append(dict(id="S21_portability_ignored_appeal_hinglish", title="Waiting period applied despite portability (appeal), Hinglish output", policy=A,
        bill=Bill(hospital="Fortis Hospital, Noida", bill_no="FHN-7012", admission_date="2026-09-20", discharge_date="2026-09-22", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 8000, 2, 4000, "2026-09-20"), L("S1", "Surgeon fee - lap hernioplasty", "surgeon", 38000, date="2026-09-20"),
                              L("A1", "Anaesthesia", "anaesthesia", 8000, date="2026-09-20"), L("O1", "OT charges", "ot", 16000, date="2026-09-20"),
                              L("I1", "Mesh (implant)", "implant", 14000, date="2026-09-20"), L("P1", "Pharmacy", "pharmacy", 6000, date="2026-09-21"),
                              L("V1", "Investigations", "investigation", 5000, date="2026-09-19")], stated_total=95000),
        discharge=Discharge(diagnosis="Bilateral inguinal hernia", procedure="Laparoscopic TEP hernioplasty", admission_type="planned"),
        rejection=Rejection(insurer="Demo General Insurance (synthetic)", letter_date="2026-10-06", claim_no="DGI/HL/26/60210", decision="rejected", amount_claimed=95000, amount_paid=0,
                            reasons=[RejectionReason(text="Claim rejected as the 24-month waiting period for hernia is not completed", clause_cited="4.2")]),
        ctx=ClaimContext(mode="appeal", patient_age=42, policy_start="2025-07-01", continuous_coverage_since="2021-03-01", user_lang="hinglish",
                         docs_provided=ALL_DOCS + ["rejection_letter", "previous_policy_copies", "implant_invoice_sticker"],
                         narrative="Insurance company ne bola waiting period complete nahi hua, par main 2021 se continuously insured hoon, bas company change ki thi."),
        patient=dict(name="Amit Verma", uhid="FHN-2290", phone="9811098110"), expected=[("waiting_period", "contestable")]))
    S.append(dict(id="S22_late_intimation_rejection_appeal", title="Rejected for late intimation (appeal)", policy=A,
        bill=Bill(hospital="Lakeview Hospital, Bengaluru", bill_no="LVH-30111", admission_date="2026-09-01", discharge_date="2026-09-04", room_category="General ward", room_days=3,
                  line_items=[L("R1", "General ward", "room", 4500, 3, 1500, "2026-09-01"), L("S1", "Surgeon fee - laparoscopic appendicectomy", "surgeon", 30000, date="2026-09-01"),
                              L("A1", "Anaesthesia", "anaesthesia", 7000, date="2026-09-01"), L("O1", "OT charges", "ot", 12000, date="2026-09-01"),
                              L("P1", "Pharmacy", "pharmacy", 9500, date="2026-09-02"), L("V1", "CT abdomen & labs", "investigation", 8000, date="2026-09-01")], stated_total=71000),
        discharge=Discharge(diagnosis="Acute appendicitis", procedure="Emergency laparoscopic appendicectomy", admission_type="emergency"),
        rejection=Rejection(insurer="Demo General Insurance (synthetic)", letter_date="2026-10-01", claim_no="DGI/HL/26/58800", decision="rejected", amount_claimed=71000, amount_paid=0,
                            reasons=[RejectionReason(text="Claim repudiated due to delay in intimation beyond 24 hours", clause_cited="5.1")]),
        ctx=ClaimContext(mode="appeal", patient_age=39, policy_start="2021-01-15", docs_provided=ALL_DOCS + ["rejection_letter"], intimation_hours_after_admission=60,
                         narrative="We informed the insurer after two and a half days because my husband went straight into emergency surgery."),
        voice=("eng", "We informed the insurer after two and a half days because my husband went straight into emergency surgery."),
        patient=dict(name="Divya Nair", uhid="LVH-55190", phone="9900223344"), expected=[("intimation", "contestable"), ("wrongful_rejection", "contestable")]))
    S.append(dict(id="S23_cashless_senior_copay", title="Cashless hernia, senior citizen — only co-pay applies", policy=Bp,
        bill=Bill(hospital="Ganga Ram Hospital, Delhi", bill_no="GRH-4041", admission_date="2026-10-01", discharge_date="2026-10-03", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 7000, 2, 3500, "2026-10-01"), L("S1", "Surgeon fee - open hernioplasty", "surgeon", 30000, date="2026-10-01"),
                              L("A1", "Anaesthesia", "anaesthesia", 7000, date="2026-10-01"), L("O1", "OT charges", "ot", 12000, date="2026-10-01"),
                              L("I1", "Mesh (implant)", "implant", 10000, date="2026-10-01"), L("P1", "Pharmacy", "pharmacy", 5000, date="2026-10-02"),
                              L("V1", "Investigations", "investigation", 4000, date="2026-09-30")], stated_total=75000),
        discharge=Discharge(diagnosis="Right inguinal hernia", procedure="Lichtenstein hernioplasty", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", patient_age=62, policy_start="2018-01-01", claim_type="cashless", docs_provided=["id_proof", "policy_copy", "claim_form_part_a"]),
        patient=dict(name="Kamla Devi", uhid="GRH-1102", phone="9810011223"), expected=[("copay", "valid")]))
    S.append(dict(id="S24_generic_pack_room_rent", title="Generic (non-India) pack — policy-only checks", policy=A,
        bill=Bill(hospital="City General Hospital", bill_no="CGH-100", admission_date="2026-09-10", discharge_date="2026-09-11", room_category="Deluxe", room_days=1,
                  line_items=[L("R1", "Deluxe room (1 day)", "room", 7500, 1, 7500, "2026-09-10"), L("N1", "Nursing charges", "nursing", 1200, date="2026-09-10"),
                              L("S1", "Surgeon fee - phacoemulsification", "surgeon", 18000, date="2026-09-10"), L("A1", "Anaesthetist fee", "anaesthesia", 4500, date="2026-09-10"),
                              L("O1", "OT charges", "ot", 9000, date="2026-09-10"), L("I1", "Foldable IOL (implant)", "implant", 16500, date="2026-09-10"),
                              L("P1", "Pharmacy & drugs", "pharmacy", 3400, date="2026-09-10"), L("V1", "Investigations", "investigation", 2600, date="2026-09-09")], stated_total=62700),
        discharge=Discharge(diagnosis="Cataract, right eye", procedure="Phacoemulsification with IOL", admission_type="day_care"),
        ctx=ClaimContext(mode="pre_submission", jurisdiction="GENERIC", patient_age=45, policy_start="2020-01-01", docs_provided=ALL_DOCS + ["implant_invoice_sticker"]),
        patient=dict(name="Alex Morgan", uhid="CGH-1", phone="0000000000"), expected=[("room_rent", "valid"), ("proportionate", "fixable"), ("missing_doc", "fixable")]))
    return S


def scenarios_challenge():
    """Complex cases the engine and prompts were NOT tuned on. Run them live with your own keys (ground truth in expected.json)."""
    A, Bp = "demo_shield", "demo_classic"
    S = []
    S.append(dict(id="C01_senior_knee_replacement_multi_cut", tier="challenge", policy=Bp, title="Senior knee replacement — room + ICU + PD + non-payables + 20% co-pay",
        blurb="Five different cuts interacting on a legacy policy",
        bill=Bill(hospital="Ortho One Hospital, Coimbatore", bill_no="OOH-5512", admission_date="2026-09-02", discharge_date="2026-09-06", room_category="Single private", room_days=4, icu_days=1,
                  line_items=[L("R1", "Single private room", "room", 22000, 4, 5500, "2026-09-02"), L("U1", "ICU (post-op)", "icu", 10000, 1, 10000, "2026-09-02"),
                              L("S1", "Surgeon fee - total knee replacement", "surgeon", 70000, date="2026-09-02"), L("A1", "Anaesthesia", "anaesthesia", 15000, date="2026-09-02"),
                              L("O1", "OT charges", "ot", 30000, date="2026-09-02"), L("I1", "Knee prosthesis (implant)", "implant", 95000, date="2026-09-02"),
                              L("P1", "Pharmacy", "pharmacy", 20000, date="2026-09-03"), L("V1", "Investigations", "investigation", 12000, date="2026-09-01"),
                              L("N1", "Nursing charges", "nursing", 6000, date="2026-09-02"), L("X1", "Knee braces", "misc", 2500, date="2026-09-05"),
                              L("X2", "Walking aids - walker", "misc", 1800, date="2026-09-05")], stated_total=284300),
        discharge=Discharge(diagnosis="Primary osteoarthritis, left knee", procedure="Total knee replacement, left", admission_type="planned", comorbidities=["osteoarthritis"],
                            history_notes="Knee pain for 5 years."),
        ctx=ClaimContext(mode="pre_submission", patient_age=67, policy_start="2020-05-01", ped_declared=["osteoarthritis"], user_lang="ta",
                         docs_provided=ALL_DOCS + ["implant_invoice_sticker"], narrative="அப்பாவுக்கு முழங்கால் மாற்று அறுவை சிகிச்சை நடந்தது."),
        patient=dict(name="Palaniswamy K", uhid="OOH-1201", phone="9842012345"),
        expected=[("non_payable", "valid"), ("room_rent", "valid"), ("icu_rent", "valid"), ("proportionate", "fixable"), ("copay", "valid"), ("missing_doc", "fixable")]))
    S.append(dict(id="C02_bariatric_undeclared_diabetes", tier="challenge", policy=A, title="Bariatric surgery with undeclared diabetes — exclusion + PED risk",
        blurb="Two claim-killing risks the bill never shows",
        bill=Bill(hospital="Apollo Spectra, Delhi", bill_no="AS-8810", admission_date="2026-08-12", discharge_date="2026-08-15", room_category="Twin sharing", room_days=3,
                  line_items=[L("R1", "Twin sharing room", "room", 12000, 3, 4000, "2026-08-12"), L("S1", "Surgeon fee - laparoscopic sleeve gastrectomy", "surgeon", 180000, date="2026-08-12"),
                              L("A1", "Anaesthesia", "anaesthesia", 30000, date="2026-08-12"), L("O1", "OT charges", "ot", 45000, date="2026-08-12"),
                              L("P1", "Pharmacy", "pharmacy", 18000, date="2026-08-13"), L("V1", "Investigations", "investigation", 15000, date="2026-08-11")], stated_total=300000),
        discharge=Discharge(diagnosis="Morbid obesity (BMI 41) with type 2 diabetes", procedure="Bariatric surgery - laparoscopic sleeve gastrectomy", admission_type="planned",
                            comorbidities=["diabetes", "obesity"], history_notes="Type 2 diabetes for 8 years on insulin.", condition_duration_months=96),
        ctx=ClaimContext(mode="pre_submission", patient_age=44, policy_start="2023-07-01", user_lang="hi", docs_provided=ALL_DOCS,
                         narrative="डॉक्टर ने वज़न घटाने की सर्जरी बताई है, क्या बीमा इसे कवर करेगा?"),
        patient=dict(name="Rakesh Arora", uhid="AS-3341", phone="9811234567"),
        expected=[("exclusion", "needs_review"), ("ped", "needs_review")]))
    S.append(dict(id="C03_package_with_extras_discount", tier="challenge", policy=A, title="Package bill with extra items and a hospital discount",
        blurb="Package rules, discount arithmetic and a hidden non-payable",
        bill=Bill(hospital="Manipal Hospital, Jaipur", bill_no="MHJ-1190", admission_date="2026-09-25", discharge_date="2026-09-26", package=True, discount=2000,
                  line_items=[L("K1", "Laparoscopic hernia repair package", "package", 65000, date="2026-09-25"), L("P1", "Pharmacy (outside package)", "pharmacy", 3000, date="2026-09-26"),
                              L("X1", "Registration charges", "misc", 300, date="2026-09-25")], stated_total=66300),
        discharge=Discharge(diagnosis="Right inguinal hernia", procedure="Laparoscopic TEP repair (package)", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", patient_age=38, policy_start="2021-04-01", docs_provided=ALL_DOCS + ["implant_invoice_sticker"]),
        patient=dict(name="Nikhil Jain", uhid="MHJ-771", phone="9414022233"),
        expected=[("non_payable", "valid")]))
    S.append(dict(id="C04_implant_wrongly_disallowed_appeal", tier="challenge", policy=A, title="Implant disallowed as 'consumable' — short-settlement appeal",
        blurb="Insurer treated a stent-like implant as a non-payable consumable", insurer_cut_categories=["implant"],
        bill=Bill(hospital="Narayana Health, Bengaluru", bill_no="NH-44120", admission_date="2026-08-03", discharge_date="2026-08-05", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 8000, 2, 4000, "2026-08-03"), L("S1", "Surgeon fee - ureteroscopy with DJ stenting", "surgeon", 40000, date="2026-08-03"),
                              L("A1", "Anaesthesia", "anaesthesia", 8000, date="2026-08-03"), L("O1", "OT charges", "ot", 15000, date="2026-08-03"),
                              L("I1", "DJ stent (implant)", "implant", 22000, date="2026-08-03"), L("P1", "Pharmacy", "pharmacy", 6000, date="2026-08-04"),
                              L("V1", "CT KUB & labs", "investigation", 7000, date="2026-08-02")], stated_total=106000),
        discharge=Discharge(diagnosis="Left ureteric calculus with hydronephrosis", procedure="Ureteroscopic lithotripsy with DJ stenting", admission_type="emergency"),
        ctx=ClaimContext(mode="appeal", patient_age=41, policy_start="2019-02-01", user_lang="kn", docs_provided=ALL_DOCS + ["rejection_letter", "implant_invoice_sticker"],
                         intimation_hours_after_admission=6),
        patient=dict(name="Shwetha Rao", uhid="NH-9911", phone="9845011223"),
        expected=[("short_settlement", "contestable")]))
    S.append(dict(id="C05_two_reasons_portability_and_docs_appeal", tier="challenge", policy=A, title="Rejected on two grounds — waiting period (ported) + missing reports",
        blurb="One ground is wrong in law, the other is curable",
        bill=Bill(hospital="Ruby Hall Clinic, Pune", bill_no="RHC-6021", admission_date="2026-09-12", discharge_date="2026-09-14", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 8000, 2, 4000, "2026-09-12"), L("S1", "Surgeon fee - lap cholecystectomy", "surgeon", 35000, date="2026-09-12"),
                              L("A1", "Anaesthesia", "anaesthesia", 8000, date="2026-09-12"), L("O1", "OT charges", "ot", 15000, date="2026-09-12"),
                              L("P1", "Pharmacy", "pharmacy", 7000, date="2026-09-13"), L("V1", "USG & LFT", "investigation", 5000, date="2026-09-11")], stated_total=78000),
        discharge=Discharge(diagnosis="Symptomatic cholelithiasis (gall bladder stones)", procedure="Laparoscopic cholecystectomy", admission_type="planned"),
        rejection=Rejection(insurer="Demo General Insurance (synthetic)", letter_date="2026-10-05", claim_no="DGI/HL/26/61500", decision="rejected", amount_claimed=78000, amount_paid=0,
                            reasons=[RejectionReason(text="Waiting period of 24 months for gall bladder stones not completed", clause_cited="4.2"),
                                     RejectionReason(text="Investigation reports not submitted", clause_cited="5.2")]),
        ctx=ClaimContext(mode="appeal", patient_age=52, policy_start="2025-08-01", continuous_coverage_since="2022-01-01", user_lang="mr",
                         docs_provided=["rejection_letter", "policy_copy", "final_itemized_bill", "discharge_summary", "previous_policy_copies"],
                         narrative="मी 2022 पासून सतत विमा घेतला आहे, फक्त कंपनी बदलली."),
        patient=dict(name="Sunita Pawar", uhid="RHC-3390", phone="9822045678"),
        expected=[("waiting_period", "contestable"), ("missing_doc", "fixable")]))
    hosp = [L("R1", "Twin sharing room", "room", 12000, 4, 3000, "2026-09-18"), L("C1", "Physician visits", "consultation", 6000, date="2026-09-18"),
            L("N1", "Nursing charges", "nursing", 4000, date="2026-09-18"), L("P1", "IV fluids", "pharmacy", 2400, date="2026-09-19"),
            L("V1", "Platelet count (3 tests)", "investigation", 1500, 3, 300, "2026-09-19")]
    pharm = [L("F1", "IV fluids", "pharmacy", 2400, date="2026-09-19"), L("F2", "Paracetamol infusion", "pharmacy", 1800, date="2026-09-19"),
             L("F3", "Antibiotics", "pharmacy", 5200, date="2026-09-20")]
    diag = [L("D1", "Dengue NS1 + IgM", "investigation", 2200, date="2026-09-18"), L("D2", "LFT, KFT", "investigation", 1600, date="2026-09-19")]
    S.append(dict(id="C06_three_bills_scanned_pharmacy_hindi", tier="challenge", policy=A, title="Three bills (one scanned) — cross-bill duplicate + arithmetic error, Hindi",
        blurb="Hospital + scanned pharmacy + diagnostics bills reconciled together",
        bill=Bill(hospital="Sanjeevani Hospital, Lucknow", bill_no="SHL-2201", admission_date="2026-09-18", discharge_date="2026-09-22", room_category="Twin sharing", room_days=4,
                  line_items=hosp + pharm + diag, stated_total=25900 + 9400 + 3800),
        parts=[("bill.pdf", hosp, 25900, "Sanjeevani Hospital, Lucknow"), ("bill_pharmacy.pdf", pharm, 9400, "Sanjeevani Medicals (scanned)", "scan"),
               ("bill_diagnostics.pdf", diag, 3800, "City Diagnostics")],
        discharge=Discharge(diagnosis="Dengue fever with warning signs", procedure="IV fluids, platelet monitoring", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=29, policy_start="2022-06-01", user_lang="hi", docs_provided=ALL_DOCS, intimation_hours_after_admission=12,
                         narrative="अस्पताल, दवा और जाँच के तीन अलग बिल हैं, कुछ चीज़ें दो बार लगी लग रही हैं।"),
        patient=dict(name="Pooja Mishra", uhid="SHL-4470", phone="9450012345"),
        expected=[("mismatch", "fixable"), ("mismatch", "fixable")]))
    S.append(dict(id="C07_copay_wrongly_applied_malayalam", tier="challenge", policy=A, title="10% co-pay deducted from a 58-year-old — short-settlement, Malayalam",
        blurb="Co-pay applies only from age 61; the insurer applied it anyway", insurer_cut_pct=10,
        bill=Bill(hospital="Aster Medcity, Kochi", bill_no="AMK-7710", admission_date="2026-09-08", discharge_date="2026-09-10", room_category="Twin sharing", room_days=2,
                  line_items=[L("R1", "Twin sharing room", "room", 8000, 2, 4000, "2026-09-08"), L("S1", "Surgeon fee - laparoscopic appendicectomy", "surgeon", 32000, date="2026-09-08"),
                              L("A1", "Anaesthesia", "anaesthesia", 7000, date="2026-09-08"), L("O1", "OT charges", "ot", 13000, date="2026-09-08"),
                              L("P1", "Pharmacy", "pharmacy", 8000, date="2026-09-09"), L("V1", "CT abdomen & labs", "investigation", 7000, date="2026-09-08")], stated_total=75000),
        discharge=Discharge(diagnosis="Acute appendicitis", procedure="Laparoscopic appendicectomy", admission_type="emergency"),
        ctx=ClaimContext(mode="appeal", patient_age=58, policy_start="2019-09-01", user_lang="ml", docs_provided=ALL_DOCS + ["rejection_letter"], intimation_hours_after_admission=5),
        patient=dict(name="Thomas Varghese", uhid="AMK-2201", phone="9847012345"), expected=[("short_settlement", "contestable")]))
    S.append(dict(id="C08_accident_late_intimation_punjabi", tier="challenge", policy=A, title="Road accident in week 1 + insurer told after 50 hours, Punjabi",
        blurb="Waiting period waived for accidents; delay is condonable; FIR still needed",
        bill=Bill(hospital="Fortis Hospital, Mohali", bill_no="FHM-3301", admission_date="2026-10-06", discharge_date="2026-10-09", room_category="Twin sharing", room_days=3,
                  line_items=[L("R1", "Twin sharing room", "room", 9000, 3, 3000, "2026-10-06"), L("S1", "Orthopaedic surgeon - tibia nailing", "surgeon", 40000, date="2026-10-06"),
                              L("A1", "Anaesthesia", "anaesthesia", 9000, date="2026-10-06"), L("O1", "OT charges", "ot", 15000, date="2026-10-06"),
                              L("I1", "Interlocking nail (implant)", "implant", 24000, date="2026-10-06"), L("P1", "Pharmacy", "pharmacy", 7000, date="2026-10-07"),
                              L("V1", "X-ray & CT", "investigation", 4000, date="2026-10-06")], stated_total=108000),
        discharge=Discharge(diagnosis="Fracture shaft of tibia following road traffic accident", procedure="Intramedullary interlocking nailing", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=33, policy_start="2026-10-01", user_lang="pa", docs_provided=ALL_DOCS + ["implant_invoice_sticker"],
                         intimation_hours_after_admission=50, narrative="ਸੜਕ ਹਾਦਸੇ ਤੋਂ ਬਾਅਦ ਸਿੱਧਾ ਆਪਰੇਸ਼ਨ ਹੋਇਆ, ਬੀਮਾ ਕੰਪਨੀ ਨੂੰ ਦੋ ਦਿਨ ਬਾਅਦ ਦੱਸਿਆ।"),
        patient=dict(name="Harpreet Singh", uhid="FHM-1180", phone="9815012345"), expected=[("intimation", "contestable"), ("missing_doc", "fixable")]))
    S.append(dict(id="C09_room_category_entitlement_odia", tier="challenge", policy="demo_category", title="Deluxe room on a 'single private AC' policy — no ₹ cap, Odia",
        blurb="Category-based entitlement; zone co-pay must not be applied",
        bill=Bill(hospital="AMRI Hospital, Bhubaneswar", bill_no="AMB-5520", admission_date="2026-09-14", discharge_date="2026-09-16", room_category="Deluxe suite", room_days=2,
                  line_items=[L("R1", "Deluxe suite", "room", 18000, 2, 9000, "2026-09-14"), L("C1", "Physician visits", "consultation", 6000, date="2026-09-14"),
                              L("N1", "Nursing charges", "nursing", 3000, date="2026-09-14"), L("P1", "Pharmacy & IV", "pharmacy", 9000, date="2026-09-15"),
                              L("V1", "Investigations", "investigation", 8000, date="2026-09-14")], stated_total=44000),
        discharge=Discharge(diagnosis="Acute gastroenteritis with severe dehydration", procedure="IV fluids", admission_type="emergency"),
        ctx=ClaimContext(mode="pre_submission", patient_age=45, policy_start="2021-01-01", user_lang="or", docs_provided=ALL_DOCS, intimation_hours_after_admission=4),
        patient=dict(name="Subhashree Das", uhid="AMB-7701", phone="9437012345"), expected=[("room_rent", "needs_review")]))
    S.append(dict(id="C10_generic_pack_spanish", tier="challenge", policy=A, title="Non-India claim on the generic pack — Spanish output",
        blurb="Policy-only checks with no Indian regulator rules",
        bill=Bill(hospital="Hospital Central", bill_no="HC-881", admission_date="2026-09-03", discharge_date="2026-09-05", room_category="Private suite", room_days=2,
                  line_items=[L("R1", "Private suite", "room", 16000, 2, 8000, "2026-09-03"), L("S1", "Surgeon fee - lap cholecystectomy", "surgeon", 40000, date="2026-09-03"),
                              L("A1", "Anaesthesia", "anaesthesia", 9000, date="2026-09-03"), L("O1", "OT charges", "ot", 15000, date="2026-09-03"),
                              L("P1", "Pharmacy", "pharmacy", 6000, date="2026-09-04"), L("V1", "Imaging & labs", "investigation", 5000, date="2026-09-02")], stated_total=91000),
        discharge=Discharge(diagnosis="Gall bladder stones with cholecystitis", procedure="Laparoscopic cholecystectomy", admission_type="planned"),
        ctx=ClaimContext(mode="pre_submission", jurisdiction="GENERIC", patient_age=50, policy_start="2020-01-01", user_lang="es",
                         docs_provided=ALL_DOCS + ["non_differential_billing_certificate"]),
        patient=dict(name="Maria Lopez", uhid="HC-12", phone="0000000000"), expected=[("room_rent", "valid"), ("proportionate", "fixable")]))
    S.append(dict(id="C11_insurer_query_letter", tier="challenge", policy=A, title="Insurer query letter — documents requested before settlement",
        blurb="Not a rejection yet: answer the query and the claim proceeds",
        bill=Bill(hospital="KIMS Hospital, Hyderabad", bill_no="KIMS-4410", admission_date="2026-09-20", discharge_date="2026-09-23", room_category="Twin sharing", room_days=3,
                  line_items=[L("R1", "Twin sharing room", "room", 12000, 3, 4000, "2026-09-20"), L("S1", "Cardiologist - PTCA", "surgeon", 50000, date="2026-09-21"),
                              L("O1", "Cath lab charges", "ot", 45000, date="2026-09-21"), L("I1", "Drug eluting stent (implant)", "implant", 35000, date="2026-09-21"),
                              L("P1", "Pharmacy", "pharmacy", 20000, date="2026-09-22"), L("V1", "Angiography & labs", "investigation", 18000, date="2026-09-20")], stated_total=180000),
        discharge=Discharge(diagnosis="Unstable angina", procedure="PTCA with DES to RCA", admission_type="emergency"),
        rejection=Rejection(insurer="Demo General Insurance (synthetic)", letter_date="2026-10-02", claim_no="DGI/HL/26/62010", decision="query", amount_claimed=180000,
                            reasons=[RejectionReason(text="Please submit the implant invoice with sticker and the investigation reports to process the claim", clause_cited="5.2")]),
        ctx=ClaimContext(mode="appeal", patient_age=55, policy_start="2018-06-01", docs_provided=["rejection_letter", "policy_copy", "final_itemized_bill", "discharge_summary"],
                         intimation_hours_after_admission=6),
        patient=dict(name="Venkat Reddy", uhid="KIMS-990", phone="9849022233"), expected=[("missing_doc", "fixable"), ("missing_doc", "fixable")]))
    S.append(dict(id="C12_cataract_kit_sublimit_senior_hinglish", tier="challenge", policy=Bp, title="Senior cataract — kit fix doesn't help because the sub-limit caps it, Hinglish",
        blurb="Three cuts where fixing one changes nothing — the agents must say so",
        bill=Bill(hospital="Centre for Sight, Delhi", bill_no="CFS-2026", admission_date="2026-09-29", discharge_date="2026-09-29", room_category="Day care", room_days=1,
                  line_items=[L("R1", "Day care bed", "room", 2000, date="2026-09-29"), L("S1", "Surgeon fee - phacoemulsification", "surgeon", 22000, date="2026-09-29"),
                              L("O1", "OT charges", "ot", 10000, date="2026-09-29"), L("I1", "Toric IOL (implant)", "implant", 21000, date="2026-09-29"),
                              L("K1", "Phaco kit", "consumable", 4000, date="2026-09-29"), L("P1", "Pharmacy", "pharmacy", 1500, date="2026-09-29"),
                              L("V1", "Biometry", "investigation", 1500, date="2026-09-28")], stated_total=62000),
        discharge=Discharge(diagnosis="Senile cataract, right eye", procedure="Phacoemulsification with toric IOL", admission_type="day_care"),
        ctx=ClaimContext(mode="pre_submission", patient_age=64, policy_start="2017-05-01", user_lang="hinglish", docs_provided=ALL_DOCS + ["implant_invoice_sticker", "kit_itemized_breakup"],
                         narrative="Papa ka cataract operation hua, bill 62 hazaar ka hai."),
        patient=dict(name="Om Prakash Gupta", uhid="CFS-110", phone="9810033344"), expected=[("non_payable", "fixable"), ("sublimit", "valid"), ("copay", "valid")]))
    return S


def insurer_pd_on_all(b) -> float:
    """What a careless TPA pays: proportionate deduction on EVERY non-room line (incl. exempt categories)."""
    from core.tools.payout import compute_payout
    from core.tools.checks import Bundle
    bill = b.bill.model_copy(deep=True)
    for it in bill.line_items:
        if it.category in ("pharmacy", "implant", "investigation", "consumable"):
            it.category = "nursing"
    return compute_payout(Bundle(policy=b.policy, bill=bill, ctx=b.ctx.model_copy(update={"mode": "pre_submission"}), discharge=b.discharge)).payout


# ----------------------------------------------------------------- edge-case upload samples (manual QA + tests)
EDGE = ROOT / "data" / "samples" / "edge_cases"


def make_edge_cases():
    import random
    EDGE.mkdir(parents=True, exist_ok=True)
    src = SC / "S01_cataract_pre_discharge" / "bill.pdf"
    d = fitz.open(src)
    d.save(EDGE / "password_protected_bill.pdf", encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="owner1234")
    (EDGE / "corrupt.pdf").write_bytes(b"%PDF-1.7\n" + bytes(random.Random(1).randrange(256) for _ in range(3000)))
    blank = fitz.open(); blank.new_page(); blank.save(EDGE / "blank_page.pdf")
    text_pdf(EDGE / "not_a_bill.pdf", "Letter", ["Dear Sir, this is a general correspondence letter with no charges or amounts.",
                                                 "We would like to inform you about our new outpatient timings from Monday to Saturday."])
    rot = fitz.open(src); rot[0].set_rotation(90); rot.save(EDGE / "rotated_bill.pdf")
    items = []
    cats = [("Pharmacy - day {d}", "pharmacy", 1850), ("Investigations - day {d}", "investigation", 2400), ("Nursing charges - day {d}", "nursing", 1200),
            ("Physician visit - day {d}", "consultation", 1500), ("Single private room - day {d}", "room", 4500), ("Gloves - day {d}", "consumable", 150)]
    for day in range(1, 11):
        for t, c, a in cats:
            items.append(L(f"M{len(items) + 1:02d}", t.format(d=day), c, a, date=f"2026-09-{day:02d}"))
    long = Bill(hospital="Long Stay Hospital, Kochi", bill_no="LSH-1", admission_date="2026-09-01", discharge_date="2026-09-11", room_category="Single private",
                room_days=10, line_items=items, stated_total=sum(i.amount for i in items))
    bill_pdf(EDGE / "long_bill_60_lines.pdf", long, {"name": "Test Patient", "uhid": "LSH-0", "phone": "9000000000"})
    png = fitz.open(src)[0].get_pixmap(dpi=40).tobytes("png")
    from PIL import Image
    import io
    Image.open(io.BytesIO(png)).convert("RGB").save(EDGE / "low_res_photo.jpg", "JPEG", quality=30)
    (EDGE / "iphone_photo.heic").write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 200)
    pol_scan = fitz.open()
    for p in fitz.open(POL_PDF):
        pg = pol_scan.new_page(); pg.insert_image(pg.rect, stream=p.get_pixmap(dpi=70).tobytes("png"))
    pol_scan.save(EDGE / "scanned_policy.pdf")
    cat_clauses = [("1.1", "Hospital", "Hospital means any institution registered with the local authorities for in-patient care."),
                   ("2.1", "Room rent", "Room rent is payable up to the actual charges of a single private AC room."),
                   ("2.2", "Co-payment", "A co-payment of 20% applies to every claim made in a city other than the zone of cover."),
                   ("3.1", "Waiting period", "Pre-existing diseases are covered after 36 months of continuous coverage.")]
    ref, objs = render_policy(EDGE / "policy_room_category.pdf", "Category Room Policy (Demo)", "DEMOHLIP26009V012627", "Category Insurance Ltd.", cat_clauses)
    print("edge cases ->", ", ".join(sorted(p.name for p in EDGE.iterdir())))


def main():
    from core.tools.checks import Bundle
    from core.tools.payout import compute_payout
    from core.detectors import detect
    pols = {"demo_shield": build_policy(), "demo_classic": build_policy_classic(), "demo_category": build_policy_category()}
    for p in pols.values():
        kb.save_policy(p)
    print("policies ->", ", ".join(pols))
    import shutil
    voice_manifest = {}
    for s in scenarios() + scenarios_extra() + scenarios_challenge():
        d = SC / s["id"]
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        pol = pols[s.get("policy", "demo_shield")]
        bill: Bill = s["bill"]; bill.source_file = "bill.pdf"
        parts = s.get("parts")
        if parts:
            side_parts = {}
            for part in parts:
                fname, items, total, hosp = part[:4]
                pb = bill.model_copy(update={"line_items": items, "stated_total": total, "hospital": hosp, "source_file": fname})
                bill_pdf(d / fname, pb, s["patient"]); side_parts[fname] = pb.model_dump()
                if len(part) > 4 and part[4] == "scan":
                    to_scan_pdf(d / fname, angle=-1.8, seed=23)
        else:
            bill_pdf(d / "bill.pdf", bill, s["patient"])
            if s.get("render") == "scan":
                to_scan_pdf(d / "bill.pdf")
            elif s.get("render") == "photo":
                to_photo(d / "bill.pdf", d / "bill.jpg")
        dis: Discharge = s["discharge"]
        text_pdf(d / "discharge.pdf", "DISCHARGE SUMMARY", [
            f"Hospital: {bill.hospital}", f"Patient Name: {s['patient']['name']}", f"UHID: {s['patient']['uhid']}  Mobile: {s['patient']['phone']}",
            f"Date of admission: {bill.admission_date}   Date of discharge: {bill.discharge_date}",
            f"Admission type: {dis.admission_type}", f"Diagnosis: {dis.diagnosis} {('(ICD-10 ' + dis.icd10 + ')') if dis.icd10 else ''}",
            f"Procedure: {dis.procedure}", f"Co-morbidities: {', '.join(dis.comorbidities) or 'None'}", f"History: {dis.history_notes or 'Nil significant'}",
            "Condition at discharge: stable. Advised follow-up in 7 days."])
        rej = s.get("rejection")
        b = Bundle(policy=pol, bill=bill, discharge=dis, rejection=rej, ctx=s["ctx"])
        if s.get("insurer_cut_pct"):
            correct = compute_payout(Bundle(policy=pol, bill=bill, ctx=s["ctx"].model_copy(update={"mode": "pre_submission"}), discharge=dis)).payout
            paid = float(round(correct * (1 - s["insurer_cut_pct"] / 100)))
            rej = Rejection(insurer=pol.insurer, letter_date="2026-09-28", claim_no="DGI/HL/26/59900", decision="partial", amount_claimed=bill.stated_total,
                            amount_paid=paid, reasons=[RejectionReason(text=f"Co-payment of {s['insurer_cut_pct']}% applied as per clause 3.5", clause_cited="3.5",
                                                                       amount=correct - paid)])
            b.rejection = rej
        if s.get("insurer_cut_categories"):
            cut = sum(i.amount for i in bill.line_items if i.category in s["insurer_cut_categories"])
            correct = compute_payout(Bundle(policy=pol, bill=bill, ctx=s["ctx"].model_copy(update={"mode": "pre_submission"}), discharge=dis)).payout
            rej = Rejection(insurer=pol.insurer, letter_date="2026-08-25", claim_no="DGI/HL/26/53310", decision="partial", amount_claimed=bill.stated_total,
                            amount_paid=float(round(correct - cut)),
                            reasons=[RejectionReason(text="Deduction of consumables / non-payable items as per policy", clause_cited="5.3", amount=cut)])
            b.rejection = rej
        if s.get("insurer_pd_on_all"):
            paid = float(round(insurer_pd_on_all(b)))
            rej = Rejection(insurer=pol.insurer, letter_date="2026-09-25", claim_no="DGI/HL/26/57002", decision="partial", amount_claimed=bill.stated_total, amount_paid=paid,
                            reasons=[RejectionReason(text="Proportionate deduction applied on all expenses as room category exceeded eligibility", clause_cited="3.3",
                                                     amount=round(compute_payout(Bundle(policy=pol, bill=bill, ctx=s["ctx"].model_copy(update={"mode": "pre_submission"}), discharge=dis)).admissible_before_copay - paid, 0))])
            b.rejection = rej
        side = {"bill": bill.model_dump(), "discharge": dis.model_dump(), "ctx": s["ctx"].model_dump(), "policy_id": pol.policy_id, "title": s["title"],
                "tier": s.get("tier", "verified"), "blurb": s.get("blurb", ""),
                "render": s.get("render", "pdf")}
        if parts:
            side["bill_parts"] = side_parts
        if rej:
            text_pdf(d / "rejection.pdf", "CLAIM SETTLEMENT / REPUDIATION LETTER", [
                f"{rej.insurer}", f"Date: {rej.letter_date}", f"Claim No: {rej.claim_no}", f"Patient Name: {s['patient']['name']}",
                f"Decision: {rej.decision}", f"Amount claimed: Rs. {rej.amount_claimed:,.0f}" + (f"   Amount approved: Rs. {rej.amount_paid:,.0f}" if rej.amount_paid is not None else "   Status: pending — information required"),
                *[f"Reason: {r.text} (Policy clause {r.clause_cited})." + (f" Deduction: Rs. {r.amount:,.0f}." if r.amount else "") for r in rej.reasons],
                "If you are not satisfied, you may write to our Grievance Redressal Officer."])
            side["rejection"] = rej.model_dump()
        if s.get("voice"):
            voice_manifest[s["id"]] = {"tts_lang": s["voice"][0], "text": s["voice"][1], "lang": s["ctx"].user_lang}
        (d / "truth.json").write_text(json.dumps(side, indent=1, ensure_ascii=False), encoding="utf-8")
        F = detect(b)
        got = sorted((f.issue_type, f.status) for f in F)
        want = sorted(s["expected"])
        p0 = compute_payout(b)
        from core.report import build as build_report
        rp = build_report(b, F, [], 0, 0, "rules")
        (d / "expected.json").write_text(json.dumps({"issues": [list(x) for x in want], "payout_as_is": p0.payout, "claimed": p0.claimed,
                                                     "payout_before": rp.payout_before, "payout_after_fixes": rp.payout_after_fixes,
                                                     "recoverable_via_appeal": rp.recoverable_via_appeal, "mode": rp.mode,
                                                     "tier": s.get("tier", "verified"), "bills": len(s.get("parts") or [1]),
                                                     "lang": s["ctx"].user_lang, "render": s.get("render", "pdf")}, indent=1), encoding="utf-8")
        flag = "OK " if got == want else "DIFF"
        print(f"{flag} {s['id']}: claimed {p0.claimed:,.0f} payout {p0.payout:,.0f}" + ("" if got == want else f"  got={got} want={want}"))
    make_edge_cases()


if __name__ == "__main__":
    main()
