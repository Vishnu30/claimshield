"""compute_payout: the single source of truth for every currency figure in ClaimShield.

Deductions are applied in insurer order; `waive` removes deductions a fix/appeal would undo.
Deductions in REDUCE_BILL change what the patient owes the hospital, not what the insurer pays.
"""
from __future__ import annotations
from core.schemas import Payout, Deduction
from core.tools import checks as C

REDUCE_BILL = {"D-DUP", "D-ARITH"}


def compute_payout(b: C.Bundle, waive: set[str] | None = None, ped_reject: bool | None = None,
                   exclusion_reject: bool = False, generic_reject: bool | None = None) -> Payout:
    waive = set(waive or ())
    fl = C.insurer_flags(b)
    ped_reject = fl["ped_reject"] if ped_reject is None else ped_reject
    generic_reject = fl["generic_reject"] if generic_reject is None else generic_reject
    bill = b.bill
    claimed = C.claimed_amount(bill)
    work = {i.id: float(i.amount) for i in bill.line_items}
    cat = {i.id: i.category for i in bill.line_items}
    deds: list[Deduction] = []

    def add(did, it, label, amt, ids=()):
        if amt > 0.5:
            deds.append(Deduction(id=did, issue_type=it, label=label, amount=round(amt, 2), item_ids=list(ids)))

    rec = C.reconcile_totals(b)
    # 1. billing integrity
    dup_amt = 0.0
    for d in rec["duplicates"]:
        dup_amt += work.get(d["item_id"], 0); work[d["item_id"]] = 0
    add("D-DUP", "mismatch", "Duplicate line items", dup_amt, [d["item_id"] for d in rec["duplicates"]])
    ar_amt = 0.0
    for a in rec["arithmetic_errors"]:
        if a["excess"] > 0 and work.get(a["item_id"], 0) > 0:
            ar_amt += a["excess"]; work[a["item_id"]] -= a["excess"]
    add("D-ARITH", "mismatch", "Qty × rate errors", ar_amt, [a["item_id"] for a in rec["arithmetic_errors"]])
    if rec["unitemized_diff"] > 0.5:
        if "D-UNITEM" in waive:
            work["__unitemized__"] = rec["unitemized_diff"]; cat["__unitemized__"] = "unitemized"
        add("D-UNITEM", "mismatch", "Bill total exceeds itemized lines", 0 if "D-UNITEM" in waive else rec["unitemized_diff"])

    # 2. non-payables
    np = C.non_payable_check(b)
    np_amt = kit_amt = 0.0
    for m in np["matches"]:
        did = "D-NP-KIT" if m["unitemized_kit"] else "D-NP"
        if did in waive:
            continue
        if m["unitemized_kit"]:
            kit_amt += work.get(m["item_id"], 0)
        else:
            np_amt += work.get(m["item_id"], 0)
        work[m["item_id"]] = 0
    add("D-NP", "non_payable", "Non-payable items (regulator list)", np_amt, [m["item_id"] for m in np["matches"] if not m["unitemized_kit"]])
    add("D-NP-KIT", "non_payable", "Un-itemized kit charges", kit_amt, [m["item_id"] for m in np["matches"] if m["unitemized_kit"]])

    # 3. room / ICU caps + proportionate deduction
    room_fixed = "D-ROOM" in waive
    for kind, did in (("room", "D-ROOM"), ("icu", "D-ICU")):
        rr = C.room_rent_check(b, kind)
        if rr.get("applicable") and did not in waive:
            tot = sum(work.get(i, 0) for i in rr["item_ids"])
            if tot > 0:
                factor = max(0.0, (tot - rr["excess"]) / tot)
                for i in rr["item_ids"]:
                    work[i] *= factor
            add(did, "room_rent" if kind == "room" else "icu_rent", f"{kind.upper()} rent above eligible limit", rr["excess"], rr["item_ids"])
    pd = C.proportionate_check(b, work)
    if pd["applicable"] and not room_fixed and "D-PD" not in waive:
        f = pd["ratio"]
        amt = 0.0
        for i in pd["associated_item_ids"]:
            cut = work.get(i, 0) * (1 - f)
            amt += cut; work[i] -= cut
        add("D-PD", "proportionate", "Proportionate deduction on associated expenses", amt, pd["associated_item_ids"])

    admissible = sum(max(0.0, v) for v in work.values()) - bill.discount

    # 4. claim-level rejections (insurer view)
    wp = C.waiting_period_check(b)
    if any(h["applies_insurer_view"] for h in wp.get("hits", [])) and "D-WAIT" not in waive:
        add("D-WAIT", "waiting_period", "Waiting period not completed (claim inadmissible)", admissible); admissible = 0
    if ped_reject and "D-PED" not in waive and admissible > 0:
        add("D-PED", "ped", "Repudiated for pre-existing disease / non-disclosure", admissible); admissible = 0
    if generic_reject and "D-REJ" not in waive and admissible > 0:
        add("D-REJ", "wrongful_rejection", "Claim repudiated by insurer", admissible); admissible = 0
    if exclusion_reject and "D-EXCL" not in waive and admissible > 0:
        add("D-EXCL", "exclusion", "Treatment excluded under policy", admissible); admissible = 0

    # 5. sub-limit
    sl = C.sublimit_check(b)
    if sl.get("matched") and sl.get("limit") and admissible > sl["limit"] and "D-SUBLIMIT" not in waive:
        add("D-SUBLIMIT", "sublimit", f"Sub-limit for {sl['procedure']}", admissible - sl["limit"]); admissible = sl["limit"]

    # 6. co-pay, sum insured
    cp = C.copay_check(b)
    copay = round(admissible * cp["pct"] / 100, 2)
    payout = admissible - copay
    si = b.si
    if si and payout > si:
        add("D-SI", "other", "Sum insured exhausted", payout - si); payout = si

    eff_claimed = claimed - sum(d.amount for d in deds if d.id in REDUCE_BILL and d.id in waive)
    # reduce-bill deductions that are waived are fixes on the hospital side
    deds = [d for d in deds if not (d.id in REDUCE_BILL and d.id in waive)]
    payout = round(max(0.0, payout), 2)
    return Payout(claimed=round(claimed, 2), admissible_before_copay=round(max(0, admissible), 2), copay=copay,
                  payout=payout, patient_pays=round(max(0.0, eff_claimed - payout), 2), deductions=deds,
                  rejected=admissible <= 0 and claimed > 0)
