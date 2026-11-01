"""ClaimShield — Streamlit app.   python -m streamlit run app/ui.py"""
from __future__ import annotations
import sys, json, urllib.parse
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
import pandas as pd
import config
from config import PATHS
from core import kb, budget, llm, appeal, escalation, agents, voice, evidence, prompts, export, snapshots
from core.ingest import InputError
from core.schemas import ClaimContext
from core.pipeline import ClaimInputs, run, ENGINES, scenario_files
from core.tools.payout import compute_payout
from core.translate import translate
from core.fmt import money
from app.components import CSS, EXTRA_CSS, chip, esc, STATUS_LABEL, stepper, verdict, action, money_bar, risk_gauge
import altair as alt
from app.i18n import t, LANG_NATIVE, has_locale, build_locale

st.set_page_config(page_title="ClaimShield — know your cut before the insurer does", page_icon="🛡️", layout="wide")
st.markdown(CSS + EXTRA_CSS, unsafe_allow_html=True)
SS = st.session_state
MODES = ["pre_discharge", "pre_submission", "appeal"]
LABELS = kb.checklists().get("labels", {})
FEATURED = ["S01_cataract_pre_discharge", "S11_scanned_cataract_gujarati", "S13_multi_bill_duplicate_bengali", "S14_pd_no_differential_kannada",
            "S20_short_settlement_pd_telugu", "S08_ped_rejection_appeal", "S21_portability_ignored_appeal_hinglish", "S22_late_intimation_rejection_appeal"]
STAGE = {"pre_discharge": "Still in hospital", "pre_submission": "Before filing", "appeal": "Appeal"}
ICON = {"pre_discharge": "🏥", "pre_submission": "📄", "appeal": "⚖️"}
GROUPS = [("fixable", "🛠️"), ("contestable", "⚖️"), ("needs_review", "🔍"), ("valid", "✔️")]
PROGRESS = [("ingest", 6, "Reading your documents"), ("extract", 22, "Reading your documents"), ("privacy", 24, "Masking personal details"),
            ("tools", 30, "Checking the bill against your policy"), ("rules", 34, "Checking the bill against your policy"),
            ("tpa", 52, "Predicting the insurer's deductions"), ("advocate", 68, "Building your counter-arguments"),
            ("jury", 80, "Independent check by three AI hosts"), ("critic", 86, "Final review"), ("verify", 90, "Recomputing every rupee"),
            ("evidence", 93, "Locating evidence on the documents"), ("localize", 97, "Writing it in your language"), ("report", 100, "Done")]

if "_pending" in SS:
    SS.update(SS.pop("_pending"))
for _k in ("pol_src", "pol_lib", "pdf_pw", "age", "si", "pstart", "ported", "cstart", "claim_type", "ped", "intim", "docs", "narr", "scen_pick"):
    if _k in SS:
        SS[_k] = SS[_k]
for k, v in {"step": 0, "mode": "pre_discharge", "age": 40, "si": 0, "pstart": date(2023, 1, 1), "cstart": date(2023, 1, 1), "intim": 0.0,
             "narr": "", "ped": "", "docs": [], "ported": False, "claim_type": "reimbursement", "pdf_pw": "", "page": "check", "scenario": None,
             "snap": None, "snap_letters": {}}.items():
    SS.setdefault(k, v)

packs = {p["code"]: p for p in kb.jurisdictions()}

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 🛡️ ClaimShield")
    SS.setdefault("jur", config.DEFAULT_JURISDICTION if config.DEFAULT_JURISDICTION in packs else list(packs)[0])
    langs = packs[SS["jur"]].get("languages", ["en"])
    if SS.get("lang") not in langs:
        SS["lang"] = langs[0]
    lang = st.selectbox("Language / भाषा", langs, format_func=lambda l: LANG_NATIVE.get(l, l), key="lang")
    if not has_locale(lang) and llm.available() and not SS.get(f"_loc_tried_{lang}"):
        SS[f"_loc_tried_{lang}"] = True
        with st.spinner(f"Translating the interface to {LANG_NATIVE.get(lang, lang)} (one time)…"):
            build_locale(lang)
    nav = {"check": "🩺 " + t("nav_check", lang), "eval": "📊 " + t("nav_eval", lang), "kb": "📚 " + t("nav_kb", lang), "about": "ℹ️ " + t("nav_about", lang)}
    page = st.radio("Menu", list(nav), format_func=nav.get, key="page", label_visibility="collapsed")
    with st.expander("⚙️ Advanced settings"):
        st.selectbox("Country / regulator", list(packs), format_func=lambda c: f"{packs[c]['name']}", key="jur")
        SS.setdefault("engine", "full" if llm.available() else "rules")
        st.selectbox("Agent depth", list(ENGINES), format_func=ENGINES.get, key="engine")
        st.toggle("Open recorded runs for samples (no credits)", value=True, key="use_snapshot")
        config.USE_SIDECAR = st.toggle("Samples: reuse ground-truth extraction", value=config.USE_SIDECAR)
        st.caption(f"Profile **{config.PROFILE}** · mode **{config.LLM_MODE}**")
        spent, caps = budget.spent(), budget.caps()
        for g, cap in caps.items():
            s = spent.get(g, 0.0)
            st.progress(min(1.0, s / cap) if cap else 0.0, text=f"{'✅' if llm.provider_ready(g) else '⚪'} {config.PROVIDER_LABEL.get(g, g)}: ${s:.2f} / ${cap:.0f}")
    for p, why in llm.DOWN.items():
        st.warning(f"{config.PROVIDER_LABEL.get(p, p)} unavailable: {why[:80]}")
    if not llm.available():
        st.info("Offline mode — deterministic engine. Recorded sample runs and digital PDFs still work.")
    st.caption(t("privacy", lang))

jcode, engine = SS["jur"], SS["engine"]
pack = packs[jcode]
cur, style = pack.get("currency", ""), pack.get("number_format", "western")
M = lambda x: money(x, cur, style)


@st.cache_data(show_spinner=False)
def catalog() -> dict:
    out = {}
    for d in sorted(PATHS["scenarios"].iterdir()):
        if not (d / "truth.json").exists():
            continue
        tr = json.loads((d / "truth.json").read_text(encoding="utf-8"))
        ex = json.loads((d / "expected.json").read_text(encoding="utf-8")) if (d / "expected.json").exists() else {}
        render, nb = tr.get("render", "pdf"), ex.get("bills", 1)
        inp = "Phone photo" if render == "photo" else "Scanned PDF" if render == "scan" else (f"{nb} bills" if nb > 1 else "Digital PDF")
        out[d.name] = {"title": tr.get("title", d.name), "blurb": tr.get("blurb", ""), "tier": tr.get("tier", "verified"), "mode": tr["ctx"]["mode"],
                       "lang": tr["ctx"].get("user_lang", "en"), "input": inp, "ex": ex}
    return out


def _sync_lang(key: str):
    if SS.get(key):
        SS["lang"] = SS[key]


def lang_chips(key: str, label: str):
    """Language chips that drive the ONE app language (sidebar, interface, results, PDFs)."""
    SS[key] = lang                                        # always mirror the current language
    st.pills(label, langs, format_func=lambda l: LANG_NATIVE.get(l, l), key=key, on_change=_sync_lang, args=(key,))


def reset_claim(**kw):
    SS.update(dict(result=None, scenario=None, snap=None, snap_letters={}, letter=None, evidence=None, chat=[], letter_tr=None,
                   _bills=None, _dis=None, _rej=None, _pol_up=None, **kw))


def load_sample(sid: str, autorun: bool = True):
    tr = json.loads((PATHS["scenarios"] / sid / "truth.json").read_text(encoding="utf-8"))
    c = tr["ctx"]
    pend = {"mode": c["mode"], "age": c.get("patient_age") or 40, "pstart": date.fromisoformat(c["policy_start"]),
            "ported": bool(c.get("continuous_coverage_since")), "cstart": date.fromisoformat(c.get("continuous_coverage_since") or c["policy_start"]),
            "ped": ", ".join(c.get("ped_declared", [])), "docs": c.get("docs_provided", []), "narr": c.get("narrative") or "",
            "claim_type": c.get("claim_type", "reimbursement"), "intim": float(c.get("intimation_hours_after_admission") or 0),
            "pol_src": "lib", "pol_lib": tr["policy_id"], "scenario": sid, "result": None, "si": 0, "jur": c.get("jurisdiction", "IN"),
            "step": 3 if autorun else 1, "autorun": autorun, "_bills": None, "_dis": None, "_rej": None, "_pol_up": None,
            "snap": None, "snap_letters": {}, "letter": None, "evidence": None, "chat": [], "letter_tr": None,
            "lang": c.get("user_lang", "en") if c.get("user_lang", "en") in packs[c.get("jurisdiction", "IN")].get("languages", []) else "en"}
    if autorun and SS.get("use_snapshot", True) and snapshots.exists(sid):
        res, letters, meta = snapshots.load(sid)
        pend.update(result=res, snap=meta, snap_letters=letters, autorun=False)
    SS["_pending"] = pend
    st.rerun()


def do_audit():
    scen_dir = PATHS["scenarios"] / SS["scenario"] if SS.get("scenario") else None
    files = scenario_files(scen_dir) if scen_dir else {"bills": [], "discharge": None, "rejection": None}
    bills = SS.get("_bills") or files["bills"]
    dis = SS.get("_dis") or files["discharge"]
    rej = (SS.get("_rej") or files["rejection"]) if SS["mode"] == "appeal" else None
    pol_id = SS.get("pol_lib") if SS.get("pol_src", "lib") == "lib" else None
    pol_file = SS.get("_pol_up") if SS.get("pol_src") == "up" else None
    if not bills:
        st.error(t("need_bill", lang)); SS["step"] = 1; return
    if not (pol_id or pol_file):
        st.error(t("need_policy", lang)); SS["step"] = 1; return
    ctx = ClaimContext(mode=SS["mode"], jurisdiction=jcode, user_lang=lang, patient_age=int(SS["age"]), sum_insured=float(SS["si"]) or None,
                       policy_start=str(SS["pstart"]), continuous_coverage_since=str(SS["cstart"]) if SS["ported"] else None,
                       ped_declared=[x.strip() for x in SS["ped"].split(",") if x.strip()], claim_type=SS["claim_type"], docs_provided=SS["docs"],
                       narrative=SS["narr"] or None, intimation_hours_after_admission=SS["intim"] or None)
    box = st.container(border=True)
    box.markdown(f"#### ⏳ {t('progress_title', lang)}")
    bar = box.progress(0, text="Starting…")
    done_box = box.empty()
    state = {"pct": 0, "label": "Starting…", "done": []}
    order = {s: (p, l) for s, p, l in PROGRESS}

    def on_event(ev):
        stg = str(ev.get("stage", "")).split(":")[0]
        if stg in order:
            pct, label = order[stg]
            if pct > state["pct"]:
                if state["label"] not in state["done"] and state["pct"] > 0:
                    state["done"].append(state["label"])
                state["pct"], state["label"] = pct, label
                try:
                    bar.progress(min(pct, 99) / 100, text=label + "…")
                    done_box.markdown("  \n".join(f"✓ {d}" for d in dict.fromkeys(state["done"])))
                except Exception:
                    pass
    try:
        res = run(ClaimInputs(ctx=ctx, policy_id=pol_id, policy_file=pol_file, bills=bills, discharge=dis, rejection=rej, engine=engine,
                              password=SS.get("pdf_pw") or None), on_event)
        bar.progress(1.0, text=f"Done in {res.seconds:.0f}s")
        SS.update(result=res, chat=[], letter=None, snap=None, step=3)
        sid = SS.get("scenario")
        if sid and res.report.llm_calls > 0 and not SS.get("_bills"):
            tier = catalog().get(sid, {}).get("tier", "verified")
            snapshots.save(sid, res, {}, config.PROFILE, local=(tier == "challenge"))   # next open is instant
    except (InputError, ValueError) as e:
        box.error(str(e)); SS["step"] = 1
    except llm.LLMUnavailable as e:
        box.error(f"AI providers unavailable: {e}. Switch Advanced → Agent depth to 'Rules engine only' and try again.")
    except Exception as e:  # noqa: BLE001
        box.error("Something went wrong."); st.exception(e)


def relocalize(res, to_lang: str):
    """Translate an existing report into another language without re-running the audit (cached per language)."""
    rep = res.report
    if to_lang == "en" or all(to_lang in f.explanations for f in rep.findings) and rep.findings:
        rep.user_lang = to_lang
        return
    with st.spinner(f"Translating results to {LANG_NATIVE.get(to_lang, to_lang)}…"):
        llm.set_stats(llm.RunStats())
        rep.findings, summary = agents.localize(rep.findings, rep.summary_en, to_lang)
        rep.summary_localized, rep.user_lang = summary, to_lang
        acts = translate({f"a{i}": a["action"] for i, a in enumerate(rep.action_plan)}, to_lang, "plan")
        for i, a in enumerate(rep.action_plan):
            a.setdefault("action_localized", {})[to_lang] = acts.get(f"a{i}", a["action"])
    if SS.get("snap"):
        snapshots.update_report(SS["snap"]["scenario"], rep)


def ftitle(f, L):
    return f.tool_evidence.get("title_localized", {}).get(L, f.title)


# ================================================================== CHECK A CLAIM
def page_check():
    step = SS["step"]
    if step == 0:
        home()
        return
    st.markdown(stepper(t("steps", lang), step), unsafe_allow_html=True)
    if step == 1:
        step_documents()
    elif step == 2:
        step_details()
    else:
        if SS.pop("autorun", False) and not SS.get("result"):
            do_audit()
        if SS.get("result"):
            results(SS["result"])
        elif SS["step"] == 3:
            st.info("Nothing to show yet.")
            if st.button(t("back", lang)):
                SS["step"] = 0; st.rerun()


def home():
    st.markdown(f'<div class="cs-h1">{esc(t("hero", lang))}</div><div class="cs-lead">{esc(t("hero_sub", lang))}</div>', unsafe_allow_html=True)

    lang_chips("lang_pick", t("explain_in", lang))
    st.markdown('<div class="cs-trust"><span><b>3</b> Meta Muse hosts · <b>12</b> agent roles</span><span>Every rupee computed, every finding cited</span>'
                '<span>Policy + IRDAI rules + ombudsman precedents</span><span>Works offline · personal data masked</span></div>', unsafe_allow_html=True)
    st.subheader(t("where", lang))
    cols = st.columns(3)
    for c, m in zip(cols, MODES):
        with c, st.container(border=True):
            st.markdown(f"#### {ICON[m]} {esc(t('m_' + m, lang))}")
            st.caption(t("d_" + m, lang))
            if st.button(t("start", lang), key=f"mode_{m}", width="stretch", type="primary"):
                reset_claim(mode=m, step=1); st.rerun()
    cat = catalog()
    ver = [k for k, v in cat.items() if v["tier"] == "verified"]
    chal = [k for k, v in cat.items() if v["tier"] == "challenge"]
    st.subheader(t("sample_claims", lang))
    tv, tc = st.tabs([f"✅ {t('verified', lang)} · {len(ver)}", f"🧪 {t('challenge', lang)} · {len(chal)}"])

    def card(sid, challenge=False):
        v, ex = cat[sid], cat[sid]["ex"]
        rec = snapshots.exists(sid)
        head = (f"{M(ex.get('recoverable_via_appeal', 0))} recoverable" if v["mode"] == "appeal"
                else f"{M(ex.get('payout_before', 0))} → {M(ex.get('payout_after_fixes', 0))}" if ex.get("payout_after_fixes", 0) > ex.get("payout_before", 0)
                else f"Pays {M(ex.get('payout_before', 0))}")
        with st.container(border=True, height=255):
            st.markdown(f"**{esc(v['title'])}**")
            st.markdown(f'<div class="cs-chips"><span>{ICON[v["mode"]]} {STAGE[v["mode"]]}</span><span class="lang">{esc(LANG_NATIVE.get(v["lang"], v["lang"]))}</span>'
                        f'<span>{esc(v["input"])}</span>{"<span>⚡ instant</span>" if rec else ""}</div>', unsafe_allow_html=True)
            if challenge:
                st.caption(v["blurb"] or "Unseen case")
            else:
                st.markdown(f'<div class="cs-headline">{esc(head)}</div>', unsafe_allow_html=True)
            label = ("Open recorded result" if rec else ("Run live" if challenge else "Open"))
            if st.button(label, key=("challenge_" if challenge else "sample_") + sid, width="stretch"):
                load_sample(sid)

    with tv:
        n = sum(1 for k in ver if snapshots.exists(k))
        st.caption(f"{t('verified_cap', lang)}  ·  ⚡ {n}/{len(ver)} open instantly from recorded Muse runs" + ("" if n else " — record them with `run snapshot`"))
        feat = [k for k in FEATURED if k in cat]
        for row in range(0, len(feat), 4):
            cols = st.columns(4)
            for c, sid in zip(cols, feat[row:row + 4]):
                with c:
                    card(sid)
        with st.expander(f"{t('all_verified', lang)} ({len(ver)})"):
            c1, c2 = st.columns([3, 1])
            pick = c1.selectbox(t("more_samples", lang), ["—"] + ver, key="scen_pick",
                                format_func=lambda k: "—" if k == "—" else f"{k.split('_')[0]} · {cat[k]['title']}")
            if c2.button("Load into the form", disabled=pick == "—", width="stretch", key="open_sample"):
                load_sample(pick, autorun=False)
    with tc:
        st.caption(t("challenge_cap", lang))
        if not llm.available():
            st.info("No API keys found — challenge samples run on the deterministic engine. Add keys to `.env` to run the Muse agents.")
        for row in range(0, len(chal), 3):
            cols = st.columns(3)
            for c, sid in zip(cols, chal[row:row + 3]):
                with c:
                    card(sid, challenge=True)
    st.subheader(t("how", lang))
    steps = [("Read", "Bills, scans, phone photos and policy pages are read by Muse vision on Baseten."),
             ("Judge", "A TPA agent and a patient-advocate agent on the Muse API argue every cut with tool calls into your policy."),
             ("Check", "Three Muse hosts vote independently; a critic on Fireworks audits; Python recomputes every rupee."),
             ("Act", "Action plan, highlighted evidence, appeal and hospital letters, escalation dates — in your language.")]
    cols = st.columns(4)
    for i, (c, (h, d)) in enumerate(zip(cols, steps), 1):
        with c, st.container(border=True):
            st.markdown(f'<span class="cs-stepnum">{i}</span> **{h}**', unsafe_allow_html=True)
            st.caption(d)


def step_documents():
    mode = SS["mode"]
    st.markdown(f"### {ICON[mode]} {t('m_' + mode, lang)}")
    st.caption(t("d_" + mode, lang))
    if SS.get("scenario"):
        st.info(f"Sample **{catalog().get(SS['scenario'], {}).get('title', SS['scenario'])}** loaded — its files are used unless you upload your own.")
    with st.container(border=True):
        st.markdown(f"**{t('doc_policy', lang)}**")
        lib = kb.policy_library()
        src = st.radio(" ", ["lib", "up"], format_func=lambda x: t("pol_" + x, lang), horizontal=True, key="pol_src", label_visibility="collapsed")
        if src == "lib":
            if lib:
                st.selectbox(t("pol_lib", lang), list(lib), format_func=lib.get, key="pol_lib", label_visibility="collapsed")
            else:
                st.warning("Policy library is empty — run `run build-kb` or upload a policy.")
        else:
            up = st.file_uploader(t("pol_up", lang), type=["pdf"], key="pol_up", label_visibility="collapsed")
            if up:
                SS["_pol_up"] = (up.name, up.getvalue())
            elif SS.get("_pol_up"):
                st.success(f"✓ {SS['_pol_up'][0]}")
            st.caption("The full policy wording works best (not only the e-card). Read once, then saved to your library.")
    with st.container(border=True):
        st.markdown(f"**{t('doc_bills', lang)}**")
        st.caption(t("doc_bills_help", lang))
        ups = st.file_uploader(t("bills", lang), type=["pdf", "png", "jpg", "jpeg", "webp", "heic"], accept_multiple_files=True, key="bills", label_visibility="collapsed")
        if ups:
            SS["_bills"] = [(f.name, f.getvalue()) for f in ups]
        if SS.get("_bills"):
            st.success("✓ " + ", ".join(n for n, _ in SS["_bills"]))
        elif SS.get("scenario"):
            st.caption("Using the sample's bill(s).")
    with st.container(border=True):
        st.markdown(f"**{t('doc_support', lang)}**")
        c1, c2 = st.columns(2)
        up = c1.file_uploader(t("discharge", lang), type=["pdf", "png", "jpg", "jpeg"], key="dis")
        if up:
            SS["_dis"] = (up.name, up.getvalue())
        if mode == "appeal":
            up = c2.file_uploader(t("rejection", lang), type=["pdf", "png", "jpg", "jpeg"], key="rej")
            if up:
                SS["_rej"] = (up.name, up.getvalue())
        with st.expander(t("locked", lang)):
            st.text_input(t("pdf_pw", lang), type="password", key="pdf_pw")
    b1, b2 = st.columns(2)
    if b1.button(t("back", lang), width="stretch"):
        SS["step"] = 0; st.rerun()
    if b2.button(t("next_details", lang), type="primary", width="stretch"):
        SS["step"] = 2; st.rerun()


def step_details():
    with st.container(border=True):
        st.markdown(f"**{t('sec_patient', lang)}**")
        c1, c2, c3 = st.columns(3)
        c1.number_input(t("age", lang), 0, 110, key="age")
        c2.selectbox(t("ctype", lang), ["reimbursement", "cashless"], key="claim_type", format_func=str.title)
        c3.number_input(t("intim", lang), 0.0, 720.0, key="intim")
    with st.container(border=True):
        st.markdown(f"**{t('sec_policy', lang)}**")
        c1, c2, c3 = st.columns(3)
        c1.date_input(t("pstart", lang), key="pstart", min_value=date(1990, 1, 1))
        c2.number_input(t("si", lang), 0, 100_000_000, step=50_000, key="si", help="Leave 0 to use the amount in the policy wording")
        c3.checkbox(t("ported", lang), key="ported")
        if SS["ported"]:
            c3.date_input(t("cstart", lang), key="cstart", min_value=date(1990, 1, 1))
    with st.expander(t("more", lang)):
        st.text_input(t("ped", lang), key="ped", placeholder="e.g. hypertension, diabetes")
        st.multiselect(t("docs", lang), list(LABELS), format_func=LABELS.get, key="docs")
        c1, c2 = st.columns([2, 1])
        c1.text_area(t("narr", lang), key="narr", height=90)
        audio = c2.audio_input(t("voice", lang), key="voice")
        if audio is not None and c2.button("Transcribe voice note"):
            with st.spinner("…"):
                tr = voice.transcribe(audio.getvalue(), "wav")
            if tr.get("text"):
                SS["_pending"] = {"narr": tr["text"]}; st.rerun()
            else:
                st.warning("Voice is unavailable right now — please type instead.")
    b1, b2 = st.columns(2)
    if b1.button(t("back", lang), width="stretch"):
        SS["step"] = 1; st.rerun()
    if b2.button("🛡️ " + t("analyze", lang), type="primary", width="stretch", key="run_audit"):
        do_audit()
        if SS["step"] == 3:
            st.rerun()


def results(res):
    rep, b = res.report, res.bundle
    sid = SS.get("scenario")
    head = catalog().get(sid, {}).get("title") if sid else (b.bill.hospital or "Your claim")
    c1, c2, c3 = st.columns([5, 1.3, 1.1])
    c1.markdown(f"### {esc(head)}")
    c1.markdown(f'<div class="cs-chips"><span>{ICON[rep.mode]} {STAGE[rep.mode]}</span><span>{esc(b.policy.product or b.policy.policy_id)}</span>'
                f'<span>{len(res.docs)} document(s)</span></div>', unsafe_allow_html=True)
    labels = {"todo": t("t_todo", lang), "findings": t("t_findings", lang), "what_do": t("what_do", lang), "collect": t("collect", lang),
              "claimed": t("k_claimed", lang), "now": t("k_now", lang), "after": t("k_after", lang), "paid": t("k_paid", lang),
              "correct": t("k_correct", lang), "risk": t("k_risk", lang)}
    c2.download_button("⬇️ " + t("dl_report", lang), export.report_pdf(rep, b.policy.product or b.policy.policy_id, b.bill.hospital, style, lang=rep.user_lang, labels=labels),
                       file_name=f"claimshield_report_{rep.user_lang}.pdf", mime="application/pdf", width="stretch")
    if c3.button("↺ " + t("new_check", lang), width="stretch"):
        reset_claim(step=0); st.rerun()
    if SS.get("snap"):
        hosts = " · ".join(f"{config.PROVIDER_LABEL.get(p, p)} {d['calls']}" for p, d in SS["snap"].get("providers", {}).items())
        a, bcol = st.columns([5, 1])
        a.markdown(f'<span class="cs-snap">⚡ {t("recorded", lang)} · {esc(SS["snap"]["recorded_at"][:10])} · {esc(hosts) or "no AI calls"} · opened instantly, no credits</span>',
                   unsafe_allow_html=True)
        if bcol.button(t("rerun", lang), disabled=not llm.available(), key="rerun_live", width="stretch"):
            SS.update(snap=None, result=None, snap_letters={}, autorun=True); st.rerun()

    # one app language: results follow it (translated in place, no re-run of the audit)
    lang_chips("lang_pick_res", t("show_in", lang))
    if rep.user_lang != lang:
        relocalize(res, lang)
    if rep.user_lang != "en" and not llm.available() and not any(rep.user_lang in f.explanations for f in rep.findings):
        st.caption("Translation needs an AI provider — showing English. Add keys to `.env` to read the results in your language.")
    L = rep.user_lang
    st.markdown(verdict(rep, M), unsafe_allow_html=True)
    summary = rep.summary_localized if L != "en" else rep.summary_en
    if summary:
        st.write(summary)
    k = st.columns(4)
    if rep.mode == "appeal":
        k[0].metric(t("k_claimed", lang), M(rep.claimed)); k[1].metric(t("k_paid", lang), M(rep.payout_before))
        k[2].metric(t("k_correct", lang), M(rep.payout_after_appeal), delta=M(rep.recoverable_via_appeal))
    else:
        k[0].metric(t("k_claimed", lang), M(rep.claimed)); k[1].metric(t("k_now", lang), M(rep.payout_before))
        k[2].metric(t("k_after", lang), M(rep.payout_after_fixes), delta=M(rep.payout_after_fixes - rep.payout_before))
    k[3].metric(t("k_risk", lang), f"{rep.risk_score}/100", delta=rep.risk_band, delta_color="off")
    st.markdown(money_bar(rep, M) + risk_gauge(rep.risk_score, rep.risk_band), unsafe_allow_html=True)

    tabs = st.tabs(["✅ " + t("t_todo", lang), "🔎 " + t("t_findings", lang), "📄 " + t("t_docs", lang), "🧮 " + t("t_whatif", lang),
                    "✉️ " + t("t_letters", lang), "💬 " + t("t_ask", lang), "🧠 " + t("t_expert", lang)])
    by_id = {f.id: f for f in rep.findings}
    with tabs[0]:
        todo_tab(rep, b, L, by_id)
    with tabs[1]:
        order = [("fixable", "🛠️"), ("contestable", "⚖️"), ("valid", "✔️"), ("needs_review", "💡")]
        money = lambda f: f.inr_impact >= 1
        groups = [(st_, ic, [f for f in rep.findings if f.status == st_ and (money(f) or st_ == "needs_review")]) for st_, ic in order]
        advisory = [f for f in rep.findings if not money(f) and f.status != "needs_review"]
        groups[-1] = ("needs_review", "💡", groups[-1][2] + advisory)
        st.markdown('<div class="cs-chips">' + "".join(f"<span>{i} {t('g_' + s_, lang)}: <b>{len(fs)}</b></span>" for s_, i, fs in groups if fs) + "</div>",
                    unsafe_allow_html=True)
        if not rep.findings:
            st.success(t("clean", lang))
        for status, icon, fs in groups:
            if not fs:
                continue
            st.markdown(f"#### {icon} {t('g_' + status, lang)}")
            for f in sorted(fs, key=lambda x: -x.inr_impact):
                label = f"{M(f.inr_impact)} · {ftitle(f, L)}" if money(f) else f"{ftitle(f, L)}"
                with st.expander(label, expanded=status in ("fixable", "contestable")):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**{t('what_happened', lang)}**")
                    c1.write(f.explanations.get(L) or f.explanation_en)
                    if f.fix:
                        c1.markdown(f"**{t('what_do', lang)}**")
                        c1.write(f.fix_localized.get(L) or f.fix)
                    if money(f):
                        c2.metric("Amount", M(f.inr_impact))
                    c2.markdown(chip(f.status if money(f) else "needs_review", None if money(f) else "Advisory") + "<br/>" + (chip("ok", "verified") if f.verified else chip("needs_review", "partly cited")), unsafe_allow_html=True)
                    c2.caption(f"confidence {f.confidence:.0%} · by {f.raised_by}")
                    with st.expander(t("sources", lang)):
                        if f.policy_cite and f.policy_cite.quote:
                            st.markdown(f'<div class="quote">Policy p.{f.policy_cite.page}: “{esc(f.policy_cite.quote[:400])}”</div>', unsafe_allow_html=True)
                        if f.irdai_cite:
                            st.markdown(f'<div class="quote reg">{esc(f.irdai_cite.doc)}: {esc(f.irdai_cite.quote[:400])} '
                                        f'{"" if f.irdai_cite.verified else chip("seed", "seed rule")}</div>', unsafe_allow_html=True)
                        for key, lab in (("tpa_reasoning", "TPA agent"), ("advocate_argument", "Advocate agent")):
                            if f.tool_evidence.get(key):
                                st.markdown(f"<span class='small'><b>{lab}:</b> {esc(f.tool_evidence[key])}</span>", unsafe_allow_html=True)
                        if f.tool_evidence.get("jury"):
                            st.markdown("<span class='small'><b>Jury:</b> " + " · ".join(f"{esc(config.PROVIDER_LABEL.get(p, p))} → {esc(v)}" for p, v in f.tool_evidence["jury"].items()) + "</span>", unsafe_allow_html=True)
                        if f.verifier_notes:
                            st.markdown("<span class='small'><b>Verifier:</b> " + esc(" · ".join(f.verifier_notes[:4])) + "</span>", unsafe_allow_html=True)
                    has_box = (f.bill_cite and f.bill_cite.bbox) or (f.policy_cite and f.policy_cite.bbox)
                    if has_box and st.button(t("highlight", lang), key=f"ev_{f.id}"):
                        SS["evidence"] = f.id
                    if SS.get("evidence") == f.id:
                        for cite in (f.bill_cite, f.policy_cite):
                            if cite and cite.bbox:
                                d = res.docs.get("policy") if cite.doc == "policy" else (res.docs.get(cite.doc) or next((x for x in res.docs.values() if x.kind == "bill"), None))
                                if d:
                                    st.image(evidence.render(d, cite.page or 1, [cite.bbox]), caption=f"{cite.doc} · page {cite.page}", width="stretch")
    with tabs[2]:
        bills = [d for d in res.docs.values() if d.kind == "bill"]
        if not bills:
            st.caption("No document previews available.")
        for d in bills:
            boxes = [f.bill_cite.bbox for f in rep.findings if f.bill_cite and f.bill_cite.bbox and f.bill_cite.doc == d.name and (f.bill_cite.page or 1) == 1]
            with st.container(border=True):
                st.markdown(f"**{esc(d.name)}** · {len(d.pages)} page(s) · {len(boxes)} highlighted line(s)")
                st.image(evidence.render(d, 1, boxes), width="stretch")
    with tabs[3]:
        st.caption("Instant and free — tick what you will fix and see the payout change.")
        cands = [f for f in rep.findings if f.deduction_ids]
        waive = set()
        with st.container(border=True):
            for f in cands:
                if st.checkbox(f"{ftitle(f, L)} ({M(f.inr_impact)})", value=f.status == "fixable", key=f"wi_{f.id}"):
                    waive |= set(f.deduction_ids)
        p, base = compute_payout(b, waive=waive), compute_payout(b)
        m1, m2, m3 = st.columns(3)
        m1.metric("Insurer pays", M(p.payout), delta=M(p.payout - base.payout))
        m2.metric("You pay the hospital", M(p.patient_pays), delta=M(p.patient_pays - base.patient_pays), delta_color="inverse")
        m3.metric("Co-pay", M(p.copay))
        rows, run_total = [{"step": "Claimed", "start": 0, "end": p.claimed, "kind": "total"}], p.claimed
        for d in p.deductions:
            rows.append({"step": d.label[:38], "start": run_total - d.amount, "end": run_total, "kind": "cut"}); run_total -= d.amount
        if p.copay:
            rows.append({"step": "Co-pay", "start": run_total - p.copay, "end": run_total, "kind": "cut"}); run_total -= p.copay
        rows.append({"step": "Insurer pays", "start": 0, "end": p.payout, "kind": "total"})
        wf = pd.DataFrame(rows); wf["order"] = range(len(wf))
        st.altair_chart(alt.Chart(wf).mark_bar(cornerRadius=3).encode(
            x=alt.X("step:N", sort=alt.SortField("order"), title=None, axis=alt.Axis(labelAngle=-30, labelLimit=160)),
            y=alt.Y("start:Q", title="₹"), y2="end:Q",
            color=alt.Color("kind:N", scale=alt.Scale(domain=["total", "cut"], range=["#0F6B5F", "#B8442F"]), legend=None),
            tooltip=["step", alt.Tooltip("end:Q", title="₹")]).properties(height=320), use_container_width=True)
    with tabs[4]:
        letters_tab(rep, b, L)
    with tabs[5]:
        chat_tab(b, L)
    with tabs[6]:
        st.caption(f"{rep.llm_calls} AI call(s) · ${rep.cost_usd:.4f} · engine {rep.engine} · Muse judges, Python computes every rupee.")
        if rep.provider_calls:
            st.dataframe(pd.DataFrame([{"host": config.PROVIDER_LABEL.get(p, p), "model": config.PROVIDERS.get(p, {}).get("model"), "calls": d["calls"],
                                        "cached": d["cached"], "tokens": d["tokens"], "cost $": round(d["usd"], 4), "roles": ", ".join(d["roles"])}
                                       for p, d in rep.provider_calls.items()]), hide_index=True, width="stretch")
        show_calls = st.toggle("Show every model call", value=False)
        with st.container(height=380):
            for ev in rep.trace:
                kind = ev.get("kind")
                if kind == "call":
                    if show_calls:
                        st.markdown(f"<div class='trace small'>☁️ {esc(ev['stage'])} → {esc(config.PROVIDER_LABEL.get(ev['provider'], ev['provider']))} · {esc(ev.get('model'))} · "
                                    f"{ev.get('tokens', 0):,} tok{' · cache' if ev.get('cached') else ''}</div>", unsafe_allow_html=True)
                elif kind == "reasoning":
                    with st.expander(f"💭 {ev['stage']} reasoning ({ev.get('provider')})"):
                        st.write(ev["text"])
                elif kind == "tool":
                    st.markdown(f"<div class='trace'>🔧 <b>{esc(ev['stage'])}</b> called <code>{esc(ev['tool'])}</code></div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='trace'><b>{esc(ev.get('stage'))}</b> · {esc(ev.get('text'))}</div>", unsafe_allow_html=True)
        st.download_button("Download full report (JSON)", rep.model_dump_json(indent=1), file_name="claimshield_report.json")


def action_gains(b, rep) -> dict:
    """What each action adds to the payout on its own — after co-pay and limits — so the rows add up to the headline."""
    base = compute_payout(b).payout
    out = {}
    for f in rep.findings:
        if f.deduction_ids:
            out[f.id] = max(0.0, compute_payout(b, waive=set(f.deduction_ids)).payout - base)
        elif f.issue_type == "short_settlement":
            out[f.id] = f.inr_impact
    return out


def todo_tab(rep, b, L, by_id):
    gains = action_gains(b, rep)
    appeal_mode = rep.mode == "appeal"
    fix_total = 0.0 if appeal_mode else max(0.0, rep.payout_after_fixes - rep.payout_before)
    contest_total = rep.recoverable_via_appeal
    kpis = []
    if not appeal_mode:
        kpis.append(("gain", t("recover_fix", lang), fix_total))
    if contest_total >= 1 or appeal_mode:
        kpis.append(("contest", t("recover_appeal", lang), contest_total))
    kpis.append(("share", t("your_share", lang), rep.patient_pays_after))
    for col, (cls, label, val) in zip(st.columns(len(kpis)), kpis):
        col.markdown(f'<div class="cs-kpi {cls}"><div class="l">{esc(label)}</div><div class="v">{esc(M(val))}</div></div>', unsafe_allow_html=True)
    text_of = lambda a: a.get("action_localized", {}).get(L) or (by_id[a["finding"]].fix_localized.get(L) if a["finding"] in by_id else None) or a["action"]
    money_rows, recs = {"fixable": [], "contestable": []}, []
    for a in rep.action_plan:
        g = gains.get(a["finding"], 0.0)
        if a["status"] in money_rows and g >= 1:
            money_rows[a["status"]].append((a, g))
        else:
            recs.append((a, g))

    def table(rows, title, total):
        if not rows:
            return
        st.markdown(f"##### {esc(title)}")
        body = "".join(f'<tr><td class="n">{n}</td><td><b>{esc(text_of(a))}</b><div class="who">{esc(a["who"])} · {esc(a["when"])}'
                       f'{" · " + esc(ftitle(by_id[a["finding"]], L)) if a["finding"] in by_id else ""}</div></td><td class="amt">+{esc(M(g))}</td></tr>'
                       for n, (a, g) in enumerate(sorted(rows, key=lambda x: -x[1]), 1))
        st.markdown(f'<table class="cs-tbl"><tr><th>#</th><th>{esc(t("what_do", lang))}</th><th class="amt">{esc(t("adds", lang))}</th></tr>{body}'
                    f'<tr class="total"><td></td><td>{esc(t("total", lang))}</td><td class="amt">+{esc(M(total))}</td></tr></table>', unsafe_allow_html=True)
        if abs(sum(g for _, g in rows) - total) > 1:
            st.caption("Fixes interact (limits, co-pay), so the total is computed for all of them together.")

    if not rep.action_plan and not rep.missing_docs:
        st.success(t("clean", lang) if not rep.findings else t("nothing_todo", lang))
    table(money_rows["fixable"], t("tbl_fix", lang), fix_total)
    table(money_rows["contestable"], t("tbl_contest", lang), contest_total if appeal_mode else sum(g for _, g in money_rows["contestable"]))
    rec_items = []
    for a, g in recs:
        why = t("no_effect", lang) if a["finding"] in gains and g < 1 and a["status"] == "fixable" else \
              (ftitle(by_id[a["finding"]], L) if a["finding"] in by_id else "")
        rec_items.append((text_of(a), f'{a["who"]} · {why}' if why else a["who"]))
    if rep.missing_docs:
        rec_items.append(("Collect these documents: " + "; ".join(m["label"] + (" (critical)" if m["critical"] else "") for m in rep.missing_docs),
                          "The insurer can hold or query the claim without them; the hospital must give medical records on request."))
    if rec_items:
        st.markdown(f"##### 💡 {esc(t('recs', lang))}")
        for n, (txt, why) in enumerate(dict.fromkeys(rec_items), 1):
            st.markdown(f'<div class="cs-rec"><b>{n}. {esc(txt)}</b><div class="why">{esc(why)}</div></div>', unsafe_allow_html=True)


def letters_tab(rep, b, L):
    kinds = []
    if any(f.status == "contestable" for f in rep.findings):
        kinds.append(("appeal", "Appeal to the insurer"))
    if any(f.status == "fixable" for f in rep.findings):
        kinds.append(("hospital", "Bill-correction request to the hospital"))
    if not kinds:
        st.success("No letter needed for this claim.")
    else:
        with st.container(border=True):
            st.markdown("**1 · Your details** — they stay on this device; the AI writes with placeholders.")
            d1, d2, d3 = st.columns(3)
            vals = {"[PATIENT_NAME]": d1.text_input("Patient / policyholder name", key="v_name"),
                    "[POLICY_NO]": d2.text_input("Policy number", key="v_pol"),
                    "[CLAIM_NO]": d3.text_input("Claim number", value=(b.rejection.claim_no if b.rejection and b.rejection.claim_no else ""), key="v_claim"),
                    "[HOSPITAL]": d1.text_input("Hospital", value=b.bill.hospital or "", key="v_hosp"),
                    "[ADMISSION_DATE]": d2.text_input("Admission date", value=b.bill.admission_date or "", key="v_adm"),
                    "[INSURER]": d3.text_input("Insurer", value=(b.rejection.insurer if b.rejection else b.policy.insurer) or "", key="v_ins"),
                    "[CONTACT]": d1.text_input("Your phone / email", key="v_contact")}
        with st.container(border=True):
            st.markdown("**2 · Choose the letter**")
            kind = st.radio("Letter", [k for k, _ in kinds], format_func=dict(kinds).get, horizontal=True, label_visibility="collapsed")
            if not SS.get("letter") and SS.get("snap_letters", {}).get(kind):
                SS["letter"] = (kind, SS["snap_letters"][kind])
            if st.button("✍️ Draft letter", type="primary"):
                with st.spinner("Writing your letter…"):
                    llm.set_stats(llm.RunStats())
                    SS["letter"] = (kind, appeal.draft(b, rep, kind)); SS["letter_tr"] = None
                    if SS.get("scenario"):
                        snapshots.update_letters(SS["scenario"], kind, SS["letter"][1])
        if SS.get("letter") and SS["letter"][0] == kind:
            letter = appeal.fill(SS["letter"][1], vals)
            with st.container(border=True):
                st.markdown("**3 · Review and send** (formal English — the insurer's language)")
                txt = st.text_area("Edit before sending", appeal.to_text(letter), height=320, label_visibility="collapsed")
                steps = escalation.timeline(b.ctx.jurisdiction, b.rejection.letter_date if b.rejection else None) if kind == "appeal" else None
                e1, e2, e3 = st.columns(3)
                e1.download_button("⬇️ PDF", appeal.to_pdf(letter, rep, b, steps), file_name=f"claimshield_{kind}.pdf", mime="application/pdf", width="stretch")
                e2.download_button("⬇️ Text", txt, file_name=f"claimshield_{kind}.txt", width="stretch")
                e3.link_button("Share on WhatsApp", "https://wa.me/?text=" + urllib.parse.quote(txt[:1500]), width="stretch")
                if L != "en":
                    if st.button(f"Show a copy in {LANG_NATIVE.get(L, L)} (to understand it)"):
                        with st.spinner("Translating…"):
                            parts = {f"p{i}": p for i, p in enumerate(txt.split("\n\n")) if p.strip()}
                            out = translate(parts, L, "letter")
                            SS["letter_tr"] = "\n\n".join(out[k] for k in parts)
                    if SS.get("letter_tr"):
                        st.text_area("Translated copy", SS["letter_tr"], height=240)
                        st.download_button(f"⬇️ PDF in {LANG_NATIVE.get(L, L)}", export.text_pdf(f"{dict(kinds)[kind]} — {LANG_NATIVE.get(L, L)}", SS["letter_tr"],
                                           "Translated copy for the family. Send the English original to the insurer."),
                                           file_name=f"claimshield_{kind}_{L}.pdf", mime="application/pdf")
    if b.ctx.mode == "appeal" or any(f.status == "contestable" for f in rep.findings):
        with st.container(border=True):
            st.markdown("**If the insurer does not fix it — escalation path**")
            steps = escalation.timeline(b.ctx.jurisdiction, b.rejection.letter_date if b.rejection else None)
            for n, s in enumerate(steps, 1):
                dates = " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in s.items() if k in ("file_by", "earliest", "expect_reply_by", "deadline"))
                st.markdown(f"{n}. **{esc(s['step'])}** — {esc(s['how'])}  \n<span class='small'>{dates}</span>", unsafe_allow_html=True)
            st.download_button("📅 Add deadlines to my calendar", escalation.to_ics(steps, (b.rejection.claim_no if b.rejection else "claim") or "claim"),
                               file_name="claimshield_deadlines.ics", mime="text/calendar")


def chat_tab(b, L):
    SS.setdefault("chat", [])
    st.caption("Answers come only from your policy's clauses and the IRDAI rules, with citations.")
    qs = ["What is my room rent limit?", "Is there a co-payment?", "What are the waiting periods?", "Which documents do I need?"]
    cols = st.columns(len(qs))
    ask = None
    for c, q in zip(cols, qs):
        if c.button(q, key=f"q_{q}", width="stretch"):
            ask = q
    for m in SS["chat"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    q = st.chat_input("Ask anything about your policy — any language") or ask
    if q:
        SS["chat"].append({"role": "user", "content": q})
        llm.set_stats(llm.RunStats())
        a = agents.ask_policy(b, q, L)
        src = ", ".join(c["id"] for c in a["sources"]["clauses"]) + ((" · " + ", ".join(r["id"] for r in a["sources"]["rules"])) if a["sources"]["rules"] else "")
        SS["chat"].append({"role": "assistant", "content": a["answer"] + f"\n\n<sub>sources: {src or '—'} · {config.PROVIDER_LABEL.get(a['provider'], a['provider'])}</sub>"})
        st.rerun()


# ================================================================== EVALUATION
def page_eval():
    st.header("📊 " + t("nav_eval", lang))
    st.write("ClaimShield is scored on **36 labelled claims** — 24 verified and 12 challenge — covering 3 policies, 3 claim stages, 12 languages and "
             "digital, scanned, photographed and multi-file bills. The ablation compares the full multi-agent pipeline with a single Muse pass.")
    rp = PATHS["eval"] / "results.json"
    data = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
    if not data:
        st.info("No results yet. Run `python eval/run_eval.py --engines rules lean full single` (or the free baseline below).")
    else:
        names = {"full": "Full (TPA + Advocate + Jury + Critic)", "lean": "Lean (Advocate)", "single": "Single Muse pass (ablation)", "rules": "Rules only (no AI)"}
        cols = st.columns(len(data))
        for c, (e, r) in zip(cols, data.items()):
            with c, st.container(border=True):
                st.markdown(f"**{names.get(e, e)}**")
                st.metric("Precision", f"{r['precision']:.2f}"); st.metric("Recall", f"{r['recall']:.2f}")
                st.caption(f"status acc {r['status_acc']:.2f} · ₹ error {r['money_error']:.0f} · citations {r['citation_validity']:.0%} · ${r.get('cost', 0):.2f}")
        st.bar_chart(pd.DataFrame({e: {"precision": r["precision"], "recall": r["recall"], "status accuracy": r["status_acc"]} for e, r in data.items()}))
        eng = st.selectbox("Per-claim detail", list(data), format_func=lambda e: names.get(e, e))
        st.dataframe(pd.DataFrame(data[eng]["per_scenario"]), hide_index=True, width="stretch")
    with st.expander("What the metrics mean"):
        st.markdown("- **Precision / recall** — issues found vs. issues in the ground truth, per issue type.\n"
                    "- **Status accuracy** — of the issues found, how often valid / fixable / contestable matches the truth.\n"
                    "- **₹ error** — total payout difference vs. ground truth (the payout engine is deterministic, so this should be 0).\n"
                    "- **Citations** — share of policy quotes that are found verbatim in the PDF.")
    if st.button("Run the free rules-engine baseline now"):
        from eval.run_eval import run_all
        with st.spinner("Evaluating…"):
            run_all(["rules"])
        st.rerun()


# ================================================================== KNOWLEDGE BASE
def page_kb():
    st.header("📚 " + t("nav_kb", lang))
    lib = kb.policy_library()
    rules = kb.rules(jcode)
    c = st.columns(5)
    c[0].metric("Policies", len(lib)); c[1].metric("Regulator rules", len(rules), help=f"{sum(1 for r in rules if not r.get('seed'))} with verbatim quotes")
    c[2].metric("Non-payable items", len(kb.non_payables(jcode))); c[3].metric("Ombudsman awards", len([p for p in kb.precedents(jcode) if not p.get("illustrative")]))
    c[4].metric("Tariff rows", len(kb.tariffs()))
    man = PATHS["kb"] / "manifest.json"
    if man.exists():
        st.caption(f"Built {json.loads(man.read_text(encoding='utf-8')).get('built_at', '')} with `run build-kb`")
    t1, t2, t3, t4 = st.tabs(["Policies", "Regulator rules", "Non-payable items", "Sources"])
    with t1:
        q = st.text_input("Search policies", placeholder="insurer or product")
        rows = []
        for pid in lib:
            try:
                p = kb.load_policy(pid)
            except Exception:
                continue
            rows.append({"insurer": p.insurer, "product": p.product, "UIN": p.uin,
                         "room rent": (f"{p.room_rent.value:g}% SI" if p.room_rent.kind == "pct_si" else M(p.room_rent.value) + "/day" if p.room_rent.kind == "fixed"
                                       else p.room_rent.category or "—"),
                         "co-pay": ", ".join(f"{c.pct:g}%" for c in p.copays) or "—",
                         "waiting (months)": ", ".join(f"{w.kind} {w.months}" for w in p.waiting_periods) or "—",
                         "exclusions": len(p.exclusions), "clauses": len(p.clauses)})
        df = pd.DataFrame(rows)
        if q and not df.empty:
            df = df[df.apply(lambda r: q.lower() in f"{r['insurer']} {r['product']}".lower(), axis=1)]
        st.dataframe(df, hide_index=True, width="stretch")
    with t2:
        topics = sorted({r["topic"] for r in rules})
        pick = st.multiselect("Topic", topics)
        st.dataframe(pd.DataFrame([{"id": r["id"], "topic": r["topic"], "rule": r["rule"], "source": r["source"], "verbatim": not r.get("seed")}
                                   for r in rules if not pick or r["topic"] in pick]), hide_index=True, width="stretch")
    with t3:
        q = st.text_input("Search items", placeholder="e.g. gloves, kit, food")
        st.dataframe(pd.DataFrame([{"item": n["item"], "list": n["list"], "treatment": n["treatment"]} for n in kb.non_payables(jcode)
                                   if not q or q.lower() in n["item"]]), hide_index=True, width="stretch")
    with t4:
        import csv
        reg = list(csv.DictReader((ROOT / "data" / "sources.csv").open(encoding="utf-8")))
        lock = (ROOT / "data" / "sources_lock.csv")
        got = {r["id"] for r in csv.DictReader(lock.open(encoding="utf-8"))} if lock.exists() else set()
        stat = json.loads((ROOT / "data" / "sources_status.json").read_text(encoding="utf-8")) if (ROOT / "data" / "sources_status.json").exists() else {}
        st.caption(f"{len(reg)} sources in `data/sources.csv` · {len(got)} downloaded · {sum(1 for v in stat.values() if v.get('status') == 'not_found')} not found (retried monthly)")
        st.dataframe(pd.DataFrame([{"id": r["id"], "category": r["category"], "status": "downloaded" if r["id"] in got else stat.get(r["id"], {}).get("status", "—"),
                                    "query": r["query"]} for r in reg]), hide_index=True, width="stretch")
        st.code("run build-kb            # identify + download + extract\nrun build-kb --discover # find new policy wordings from 30 insurers\nrun build-kb --fast     # parser only, $0", language="bash")


# ================================================================== ABOUT
def page_about():
    st.header("ℹ️ ClaimShield")
    t1, t2, t3, t4 = st.tabs(["How it works", "Where Muse runs", "Privacy & safety", "Limits"])
    with t1:
        st.markdown("**Muse judges, Python computes.** Every amount comes from one deterministic payout engine; every finding carries a policy clause or an IRDAI rule.")
        st.dataframe(pd.DataFrame([
            {"stage": "Read", "what happens": "Bills (PDF, scans, photos), discharge summary, rejection letter, policy wording", "engine": "Muse vision + deterministic parsers"},
            {"stage": "TPA Adjudicator", "what happens": "Predicts every deduction the insurer will make, with tool calls", "engine": "Muse API"},
            {"stage": "Patient Advocate", "what happens": "valid / fixable / contestable per cut, with the rule and the fix", "engine": "Muse API"},
            {"stage": "Jury", "what happens": "Three hosts vote independently, in parallel", "engine": "Muse API · Baseten · Fireworks"},
            {"stage": "Critic + Verifier", "what happens": "Audit; Python recomputes money, validates quotes, drops uncited claims", "engine": "Fireworks + Python"},
            {"stage": "Report", "what happens": "Action plan, evidence, letters, escalation, any of 11 languages", "engine": "Python + Muse"}]),
            hide_index=True, width="stretch")
    with t2:
        st.write("Three hosts, one job each: **Muse API** is the brain (judgement), **Baseten** the eyes (reading), **Fireworks** the independent checker and the voice to the user.")
        st.dataframe(pd.DataFrame([{"role": config.ROLE_INFO[r][0], "primary": config.PROVIDER_LABEL.get(c[0], c[0]),
                                    "failover": " → ".join(config.PROVIDER_LABEL.get(x, x) for x in c[1:]),
                                    "ready": "✅" if any(llm.provider_ready(x) for x in c) else "⚪", "why": config.ROLE_INFO[r][1]}
                                   for r, c in config.ROUTES.items() if r in config.ROLE_INFO]), hide_index=True, width="stretch")
    with t3:
        st.markdown("- Names, phone numbers, Aadhaar, PAN and email are **masked before any AI call**.\n- Uploaded files are processed in memory.\n"
                    "- Letters are drafted with placeholders and filled on your device.\n- Every provider has a hard spend cap; identical requests are cached.")
    with t4:
        st.markdown("- Decision support, not legal or medical advice.\n- Regulator rules marked *seed* are paraphrased until `run build-kb` replaces them with verbatim quotes.\n"
                    "- Illustrative precedents are never cited in letters.")


{"check": page_check, "eval": page_eval, "kb": page_kb, "about": page_about}[page]()
