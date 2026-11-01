"""Pydantic data contracts shared by every stage. Unknown values are None, never guessed."""
from __future__ import annotations
from datetime import date
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field

IssueType = Literal["room_rent", "icu_rent", "proportionate", "waiting_period", "ped", "non_payable", "sublimit",
                    "copay", "mismatch", "missing_doc", "overbilling", "exclusion", "wrongful_rejection",
                    "short_settlement", "intimation", "other"]
Status = Literal["valid", "contestable", "fixable", "needs_review"]
Mode = Literal["pre_discharge", "pre_submission", "appeal"]
Category = Literal["room", "icu", "nursing", "consultation", "surgeon", "anaesthesia", "ot", "procedure", "implant",
                   "pharmacy", "consumable", "investigation", "package", "ambulance", "misc", "unitemized"]


class ClauseRef(BaseModel):
    clause_id: Optional[str] = None
    page: Optional[int] = None
    quote: Optional[str] = None


class Limit(BaseModel):
    kind: Literal["pct_si", "fixed", "category", "none"] = "none"
    value: Optional[float] = None          # pct (e.g. 1.0 = 1% of SI) or fixed INR/day
    max_value: Optional[float] = None      # cap on pct-based limit
    category: Optional[str] = None         # e.g. "single private AC room"
    cite: ClauseRef = ClauseRef()


class Copay(BaseModel):
    pct: float
    condition: str = "all claims"
    age_min: Optional[int] = None
    cite: ClauseRef = ClauseRef()


class Sublimit(BaseModel):
    procedure: str
    aliases: list[str] = []
    limit: Optional[float] = None
    pct_si: Optional[float] = None
    basis: str = "per_claim"
    cite: ClauseRef = ClauseRef()


class WaitingPeriod(BaseModel):
    kind: Literal["initial", "specific", "ped"]
    months: int
    conditions: list[str] = []
    cite: ClauseRef = ClauseRef()


class Exclusion(BaseModel):
    code: Optional[str] = None
    text: str
    keywords: list[str] = []
    cite: ClauseRef = ClauseRef()


class Clause(BaseModel):
    id: str
    title: str = ""
    page: int
    text: str


class PolicyRules(BaseModel):
    policy_id: str
    insurer: Optional[str] = None
    product: Optional[str] = None
    uin: Optional[str] = None
    jurisdiction: str = "IN"
    sum_insured: Optional[float] = None
    room_rent: Limit = Limit()
    icu_rent: Limit = Limit()
    proportionate_deduction: bool = True
    proportionate_cite: ClauseRef = ClauseRef()
    copays: list[Copay] = []
    sublimits: list[Sublimit] = []
    waiting_periods: list[WaitingPeriod] = []
    exclusions: list[Exclusion] = []
    intimation_emergency_hours: Optional[int] = None
    intimation_planned_days_before: Optional[int] = None
    claim_submission_days: Optional[int] = None
    clauses: list[Clause] = []
    source_file: Optional[str] = None


class LineItem(BaseModel):
    id: str
    description: str
    category: Category = "misc"
    qty: float = 1
    rate: Optional[float] = None
    amount: float
    date: Optional[str] = None
    page: int = 1
    doc: str = "bill"                     # which uploaded file (for evidence rendering)


class Bill(BaseModel):
    hospital: Optional[str] = None
    bill_no: Optional[str] = None
    admission_date: Optional[str] = None
    discharge_date: Optional[str] = None
    room_category: Optional[str] = None
    room_days: Optional[float] = None
    icu_days: Optional[float] = None
    line_items: list[LineItem] = []
    discount: float = 0
    stated_total: Optional[float] = None
    package: bool = False
    differential_billing: Optional[bool] = None   # None = unknown; False = hospital charges same regardless of room
    source_file: Optional[str] = None


class Discharge(BaseModel):
    diagnosis: Optional[str] = None
    procedure: Optional[str] = None
    icd10: Optional[str] = None
    admission_type: Optional[Literal["emergency", "planned", "day_care"]] = None
    comorbidities: list[str] = []
    history_notes: Optional[str] = None
    condition_duration_months: Optional[int] = None
    source_file: Optional[str] = None


class RejectionReason(BaseModel):
    text: str
    clause_cited: Optional[str] = None
    amount: Optional[float] = None


class Rejection(BaseModel):
    insurer: Optional[str] = None
    letter_date: Optional[str] = None
    claim_no: Optional[str] = None
    decision: Literal["rejected", "partial", "query"] = "rejected"
    amount_claimed: Optional[float] = None
    amount_paid: Optional[float] = None
    reasons: list[RejectionReason] = []
    source_file: Optional[str] = None


class ClaimContext(BaseModel):
    mode: Mode = "pre_submission"
    jurisdiction: str = "IN"
    user_lang: str = "en"
    patient_age: Optional[int] = None
    sum_insured: Optional[float] = None               # from the policy schedule / e-card; overrides the wording
    policy_start: Optional[str] = None
    continuous_coverage_since: Optional[str] = None   # earliest date incl. portability / renewals
    ped_declared: list[str] = []
    claim_type: Literal["cashless", "reimbursement"] = "reimbursement"
    city_tier: Optional[str] = None
    docs_provided: list[str] = []
    narrative: Optional[str] = None
    narrative_lang: Optional[str] = None
    intimation_hours_after_admission: Optional[float] = None


class Citation(BaseModel):
    doc: str
    page: Optional[int] = None
    quote: Optional[str] = None
    bbox: Optional[list[float]] = None
    verified: bool = False


class Finding(BaseModel):
    id: str
    issue_type: IssueType
    title: str
    raised_by: Literal["tpa", "advocate", "rules", "critic"] = "rules"
    status: Status = "needs_review"
    inr_impact: float = 0
    deduction_ids: list[str] = []          # engine deductions this finding controls
    confidence: float = 0.8
    explanation_en: str = ""
    explanations: dict[str, str] = {}
    fix: Optional[str] = None
    fix_localized: dict[str, str] = {}
    policy_cite: Optional[Citation] = None
    bill_cite: Optional[Citation] = None
    irdai_cite: Optional[Citation] = None
    precedent_ids: list[str] = []
    tool_evidence: dict[str, Any] = {}
    verified: bool = False
    verifier_notes: list[str] = []


class Deduction(BaseModel):
    id: str
    issue_type: IssueType
    label: str
    amount: float
    item_ids: list[str] = []


class Payout(BaseModel):
    claimed: float
    admissible_before_copay: float
    copay: float
    payout: float
    patient_pays: float
    deductions: list[Deduction] = []
    rejected: bool = False


class ClaimReport(BaseModel):
    mode: Mode
    jurisdiction: str = "IN"
    user_lang: str = "en"
    currency: str = "₹"
    claimed: float = 0
    payout_before: float = 0
    payout_after_fixes: float = 0
    payout_after_appeal: float = 0
    recoverable_via_appeal: float = 0
    patient_pays_before: float = 0
    patient_pays_after: float = 0
    insurer_paid: Optional[float] = None
    risk_score: int = 0
    risk_band: str = "low"
    summary_en: str = ""
    summary_localized: str = ""
    findings: list[Finding] = []
    missing_docs: list[dict] = []
    action_plan: list[dict] = []
    trace: list[dict] = []
    cost_usd: float = 0
    llm_calls: int = 0
    engine: str = "full"
    provider_calls: dict[str, dict] = {}
    generated_at: str = ""
    payout_detail_before: Optional[Payout] = None
