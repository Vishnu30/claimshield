"""UI strings. Results text is localized by Muse; this file only covers the app chrome (English + Hindi)."""
S = {
    "tagline": {"en": "Know what your insurer will cut — before they do.", "hi": "बीमा कंपनी क्या काटेगी — उनसे पहले जानिए।"},
    "sub": {"en": "Upload your policy and hospital bill. ClaimShield's AI agents audit every line against your policy and the IRDAI rules, tell you what to fix, and write the appeal if your claim was rejected.",
            "hi": "अपनी पॉलिसी और अस्पताल का बिल अपलोड करें। ClaimShield के AI एजेंट हर लाइन को पॉलिसी और IRDAI नियमों से जाँचते हैं, बताते हैं क्या ठीक करना है, और क्लेम रिजेक्ट होने पर अपील लिखते हैं।"},
    "where": {"en": "Where is your claim right now?", "hi": "आपका क्लेम अभी किस स्टेज पर है?"},
    "m_pre_discharge": {"en": "Still in hospital", "hi": "अभी अस्पताल में हैं"},
    "d_pre_discharge": {"en": "Check the bill before discharge, while it can still be corrected.", "hi": "डिस्चार्ज से पहले बिल जाँचें, जब तक उसे ठीक कराया जा सकता है।"},
    "m_pre_submission": {"en": "About to file a claim", "hi": "क्लेम फाइल करने वाले हैं"},
    "d_pre_submission": {"en": "See the likely payout and missing documents before you submit.", "hi": "जमा करने से पहले संभावित भुगतान और कमी वाले दस्तावेज़ देखें।"},
    "m_appeal": {"en": "Claim rejected or cut", "hi": "क्लेम रिजेक्ट या कम हुआ"},
    "d_appeal": {"en": "Find what is wrong in the decision and get a ready-to-send appeal.", "hi": "फैसले में गलती ढूँढें और भेजने लायक अपील पाएँ।"},
    "samples": {"en": "Or try a sample case (no upload needed)", "hi": "या एक सैंपल केस आज़माएँ (अपलोड की ज़रूरत नहीं)"},
    "more_samples": {"en": "All 24 sample cases", "hi": "सभी 24 सैंपल केस"},
    "steps": {"en": ["Situation", "Documents", "Details", "Results"], "hi": ["स्थिति", "दस्तावेज़", "जानकारी", "नतीजे"]},
    "policy": {"en": "Your health insurance policy", "hi": "आपकी हेल्थ इंश्योरेंस पॉलिसी"},
    "pol_lib": {"en": "Choose from library", "hi": "सूची से चुनें"},
    "pol_up": {"en": "Upload policy PDF", "hi": "पॉलिसी PDF अपलोड करें"},
    "bills": {"en": "Hospital bill(s) — PDF or clear phone photos", "hi": "अस्पताल का बिल — PDF या साफ़ फ़ोटो"},
    "discharge": {"en": "Discharge summary (optional, improves the check)", "hi": "डिस्चार्ज समरी (वैकल्पिक, जाँच बेहतर होती है)"},
    "rejection": {"en": "Rejection / settlement letter", "hi": "रिजेक्शन / सेटलमेंट लेटर"},
    "pdf_pw": {"en": "PDF password (only if your file is locked)", "hi": "PDF पासवर्ड (सिर्फ़ अगर फ़ाइल लॉक है)"},
    "back": {"en": "Back", "hi": "पीछे"}, "next": {"en": "Next", "hi": "आगे"},
    "age": {"en": "Patient's age", "hi": "मरीज़ की उम्र"},
    "si": {"en": "Sum insured (₹, from policy card)", "hi": "बीमा राशि (₹, पॉलिसी कार्ड से)"},
    "pstart": {"en": "Current policy start date", "hi": "मौजूदा पॉलिसी शुरू होने की तारीख"},
    "ported": {"en": "Ported or continuously insured from an earlier policy", "hi": "पहले की पॉलिसी से पोर्ट / लगातार बीमित"},
    "cstart": {"en": "Insured continuously since", "hi": "कब से लगातार बीमित"},
    "ctype": {"en": "Claim type", "hi": "क्लेम का प्रकार"},
    "more": {"en": "More details (optional — better accuracy)", "hi": "और जानकारी (वैकल्पिक — बेहतर सटीकता)"},
    "ped": {"en": "Illnesses declared when buying the policy", "hi": "पॉलिसी लेते समय बताई गई बीमारियाँ"},
    "intim": {"en": "Hours after admission the insurer was informed (0 = don't know)", "hi": "भर्ती के कितने घंटे बाद बीमा कंपनी को बताया (0 = पता नहीं)"},
    "docs": {"en": "Documents you already have", "hi": "आपके पास मौजूद दस्तावेज़"},
    "narr": {"en": "Tell us in your own words — any language", "hi": "अपने शब्दों में बताइए — किसी भी भाषा में"},
    "voice": {"en": "Or record a voice note", "hi": "या वॉइस नोट रिकॉर्ड करें"},
    "run": {"en": "Check my claim", "hi": "मेरा क्लेम जाँचें"},
    "running": {"en": "ClaimShield agents are checking your claim…", "hi": "ClaimShield एजेंट आपका क्लेम जाँच रहे हैं…"},
    "plan": {"en": "Your action plan", "hi": "आपकी कार्य-योजना"},
    "new": {"en": "Start a new check", "hi": "नई जाँच शुरू करें"},
    "dl_report": {"en": "Download report (PDF)", "hi": "रिपोर्ट डाउनलोड करें (PDF)"},
    "t_findings": {"en": "What we found", "hi": "क्या मिला"}, "t_whatif": {"en": "What if", "hi": "अगर ऐसा हो"},
    "t_letters": {"en": "Letters & appeal", "hi": "पत्र और अपील"}, "t_ask": {"en": "Ask your policy", "hi": "पॉलिसी से पूछें"},
    "t_how": {"en": "How we decided", "hi": "हमने कैसे तय किया"},
    "nav_check": {"en": "Check a claim", "hi": "क्लेम जाँचें"}, "nav_eval": {"en": "Evaluation", "hi": "मूल्यांकन"},
    "nav_kb": {"en": "Knowledge base", "hi": "ज्ञान-कोष"}, "nav_about": {"en": "About & privacy", "hi": "जानकारी और गोपनीयता"},
    "need_bill": {"en": "Please add at least one hospital bill.", "hi": "कृपया कम से कम एक अस्पताल बिल जोड़ें।"},
    "need_policy": {"en": "Please choose or upload your policy.", "hi": "कृपया अपनी पॉलिसी चुनें या अपलोड करें।"},
    "privacy": {"en": "Names, phone, Aadhaar and PAN are masked before any AI call. Decision support, not legal advice.",
                "hi": "AI को भेजने से पहले नाम, फ़ोन, आधार और PAN छिपा दिए जाते हैं। यह निर्णय-सहायता है, कानूनी सलाह नहीं।"},
    "clean": {"en": "No issues found — this claim looks clean.", "hi": "कोई समस्या नहीं मिली — क्लेम ठीक लगता है।"},
}


S.update({
    "hero": {"en": "Know what your health insurer will cut — before they do.", "hi": "बीमा कंपनी क्या काटेगी — उनसे पहले जानिए।"},
    "hero_sub": {"en": "ClaimShield's AI agents audit your hospital bill line by line against your policy and IRDAI rules, show you what to fix, and write the appeal if your claim was rejected.",
                 "hi": "ClaimShield के AI एजेंट आपके अस्पताल बिल की हर लाइन को पॉलिसी और IRDAI नियमों से जाँचते हैं, बताते हैं क्या ठीक करना है, और क्लेम रिजेक्ट होने पर अपील लिखते हैं।"},
    "explain_in": {"en": "Explanations in your language", "hi": "आपकी भाषा में समझाया जाता है"},
    "start": {"en": "Start", "hi": "शुरू करें"}, "open": {"en": "Open", "hi": "खोलें"}, "run_live": {"en": "Run live", "hi": "लाइव चलाएँ"},
    "sample_claims": {"en": "Sample claims", "hi": "सैंपल क्लेम"},
    "verified": {"en": "Verified samples", "hi": "सत्यापित सैंपल"}, "challenge": {"en": "Challenge samples", "hi": "चुनौती सैंपल"},
    "verified_cap_snap": {"en": "Recorded live runs of the Muse agents — open instantly, no API keys needed.", "hi": "Muse एजेंटों के रिकॉर्ड किए गए रन — तुरंत खोलें, API key की ज़रूरत नहीं।"},
    "verified_cap": {"en": "Ground-truth claims used to evaluate ClaimShield. Open them offline, or live with your keys.", "hi": "मूल्यांकन के लिए प्रयुक्त क्लेम। ऑफ़लाइन या अपनी key से लाइव खोलें।"},
    "challenge_cap": {"en": "New, complex claims the agents were not tuned on. Add your own keys to .env and run them live.", "hi": "नए, जटिल क्लेम जिन पर एजेंट ट्यून नहीं हुए। .env में अपनी key डालें और लाइव चलाएँ।"},
    "all_verified": {"en": "All verified samples", "hi": "सभी सत्यापित सैंपल"},
    "how": {"en": "How it works", "hi": "यह कैसे काम करता है"},
})

S.update({
    "doc_policy": {"en": "1 · Your policy", "hi": "1 · आपकी पॉलिसी"},
    "doc_bills": {"en": "2 · Hospital bill(s)", "hi": "2 · अस्पताल के बिल"},
    "doc_support": {"en": "3 · Supporting documents (optional)", "hi": "3 · सहायक दस्तावेज़ (वैकल्पिक)"},
    "doc_bills_help": {"en": "Final itemized bill. Several files are fine (hospital, pharmacy, lab). PDF or clear phone photo.",
                       "hi": "अंतिम आइटमवार बिल। कई फ़ाइलें चल जाएँगी (अस्पताल, फ़ार्मेसी, लैब)। PDF या साफ़ फ़ोटो।"},
    "locked": {"en": "Is a file password-protected?", "hi": "क्या कोई फ़ाइल पासवर्ड से सुरक्षित है?"},
    "next_details": {"en": "Next: patient details", "hi": "आगे: मरीज़ की जानकारी"},
    "sec_patient": {"en": "Patient", "hi": "मरीज़"}, "sec_policy": {"en": "Policy", "hi": "पॉलिसी"},
    "analyze": {"en": "Analyze my claim", "hi": "मेरा क्लेम जाँचें"},
    "k_claimed": {"en": "Claimed", "hi": "दावा"}, "k_now": {"en": "Insurer pays now", "hi": "अभी बीमा कंपनी देगी"},
    "k_after": {"en": "After the fixes", "hi": "सुधार के बाद"}, "k_correct": {"en": "Correct payout", "hi": "सही भुगतान"},
    "k_paid": {"en": "Insurer paid", "hi": "बीमा कंपनी ने दिया"}, "k_risk": {"en": "Claim risk", "hi": "क्लेम जोखिम"},
    "show_in": {"en": "Show results in", "hi": "नतीजे इस भाषा में"},
    "t_todo": {"en": "What to do", "hi": "क्या करें"}, "t_docs": {"en": "Documents", "hi": "दस्तावेज़"}, "t_expert": {"en": "Expert view", "hi": "विशेषज्ञ दृश्य"},
    "g_fixable": {"en": "Fix now", "hi": "अभी ठीक करें"}, "g_contestable": {"en": "Contest with the insurer", "hi": "बीमा कंपनी से चुनौती दें"},
    "g_needs_review": {"en": "Advisory (no direct cost)", "hi": "सलाह (सीधा ख़र्च नहीं)"}, "g_valid": {"en": "Valid deductions (you pay these)", "hi": "सही कटौतियाँ (ये आप देंगे)"},
    "what_happened": {"en": "What happened", "hi": "क्या हुआ"}, "what_do": {"en": "What to do", "hi": "क्या करें"},
    "sources": {"en": "Why — policy clause, rule and agent reasoning", "hi": "क्यों — पॉलिसी क्लॉज़, नियम और एजेंट का तर्क"},
    "highlight": {"en": "Show where on the document", "hi": "दस्तावेज़ पर दिखाएँ"},
    "collect": {"en": "Documents to collect", "hi": "जुटाने वाले दस्तावेज़"},
    "nothing_todo": {"en": "Nothing to change — the remaining cuts are valid under your policy.", "hi": "कुछ बदलने की ज़रूरत नहीं — बाकी कटौतियाँ पॉलिसी के अनुसार सही हैं।"},
    "progress_title": {"en": "Analyzing your claim", "hi": "आपके क्लेम की जाँच हो रही है"},
    "recorded": {"en": "Recorded live run", "hi": "रिकॉर्ड किया गया लाइव रन"}, "rerun": {"en": "Re-run live", "hi": "फिर से लाइव चलाएँ"},
    "new_check": {"en": "New check", "hi": "नई जाँच"},
    "recover_fix": {"en": "Recover with fixes", "hi": "सुधार से वापस"}, "recover_appeal": {"en": "Recover by appeal", "hi": "अपील से वापस"},
    "your_share": {"en": "You still pay", "hi": "फिर भी आप देंगे"},
    "tbl_fix": {"en": "Fixes that add money to your payout", "hi": "सुधार जो भुगतान बढ़ाते हैं"},
    "tbl_contest": {"en": "Grounds to contest with the insurer", "hi": "बीमा कंपनी से चुनौती के आधार"},
    "recs": {"en": "Recommended next steps (no direct cost)", "hi": "सुझाए गए अगले कदम (सीधा ख़र्च नहीं)"},
    "adds": {"en": "Adds to payout", "hi": "भुगतान में जुड़ेगा"}, "total": {"en": "Total", "hi": "कुल"},
    "no_effect": {"en": "No change to the payout here (a limit caps it) — still worth doing", "hi": "यहाँ भुगतान नहीं बदलेगा (सीमा लागू) — फिर भी करें"},
})

LANG_NATIVE = {"en": "English", "hi": "हिन्दी", "hinglish": "Hinglish", "gu": "ગુજરાતી", "mr": "मराठी", "kn": "ಕನ್ನಡ", "ta": "தமிழ்", "te": "తెలుగు",
               "bn": "বাংলা", "ml": "മലയാളം", "pa": "ਪੰਜਾਬੀ", "or": "ଓଡ଼ିଆ", "es": "Español", "fr": "Français", "ar": "العربية", "pt": "Português",
               "id": "Bahasa Indonesia", "sw": "Kiswahili"}


import json as _json
from pathlib import Path as _Path
LOCALES = _Path(__file__).resolve().parent / "locales"
_LOADED: dict[str, dict] = {}


def _locale(lang: str) -> dict:
    if lang not in _LOADED:
        f = LOCALES / f"{lang}.json"
        _LOADED[lang] = _json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return _LOADED[lang]


def has_locale(lang: str) -> bool:
    return lang in ("en", "hi") or bool(_locale(lang))


def build_locale(lang: str) -> bool:
    """Translate the whole interface once with Muse and save app/locales/<lang>.json (commit it)."""
    from core.translate import translate
    src = {k: ("|".join(v["en"]) if isinstance(v["en"], list) else v["en"]) for k, v in S.items()}
    out = translate(src, lang, "ui")
    got = {k: v for k, v in out.items() if v != src[k]}
    if len(got) < len(src) // 2:
        return False
    LOCALES.mkdir(exist_ok=True)
    (LOCALES / f"{lang}.json").write_text(_json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    _LOADED[lang] = out
    return True


def t(key: str, lang: str):
    v = S.get(key, {})
    if lang == "hi" and "hi" in v:
        return v["hi"]
    loc = _locale(lang) if lang not in ("en", "hi") else {}
    if key in loc:
        return loc[key].split("|") if isinstance(v.get("en"), list) else loc[key]
    return v.get("en", key)
