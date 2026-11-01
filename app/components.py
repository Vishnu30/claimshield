"""Presentation helpers for the Streamlit UI."""
from __future__ import annotations
import html
from core.fmt import money

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Mukta:wght@400;500;600;700&display=swap');
:root{--ink:#10262B;--mist:#F4F7F6;--paper:#FFFFFF;--teal:#0F6B5F;--mari:#E9A23B;--cut:#B8442F;--slate:#5E6E72;--line:#DCE3E1;}
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select {font-family:'Mukta','Noto Sans',system-ui,sans-serif !important;}
.block-container{padding-top:3.4rem;max-width:1240px;}
h1,h2,h3{color:var(--ink);letter-spacing:-0.01em;}
.cs-brand{display:flex;align-items:baseline;gap:.8rem;margin-bottom:.2rem}
.cs-brand .name{font-size:2.05rem;font-weight:700;color:var(--ink)}
.cs-brand .tag{color:var(--slate);font-size:1.02rem}
.cs-hero{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:1.2rem 1.4rem 1rem;margin:.4rem 0 1rem}
.cs-nums{display:flex;flex-wrap:wrap;gap:2.2rem;align-items:flex-end;margin-bottom:.9rem}
.cs-num .v{font-size:2.1rem;font-weight:700;line-height:1;font-variant-numeric:tabular-nums;color:var(--ink)}
.cs-num .l{color:var(--slate);font-size:.92rem;margin-top:.25rem}
.cs-num.big .v{font-size:2.9rem;color:var(--teal)}
.cs-arrow{font-size:1.8rem;color:var(--slate);padding-bottom:.6rem}
.cs-bar{display:flex;height:30px;border-radius:8px;overflow:hidden;border:1px solid var(--line);background:#fff}
.cs-bar div{height:100%}
.cs-legend{display:flex;flex-wrap:wrap;gap:1.2rem;margin-top:.5rem;font-size:.9rem;color:var(--slate)}
.cs-legend span:before{content:"";display:inline-block;width:.8rem;height:.8rem;border-radius:3px;margin-right:.35rem;vertical-align:-1px;background:var(--c)}
.chip{display:inline-block;padding:.05rem .55rem;border-radius:999px;font-size:.8rem;font-weight:600;border:1px solid transparent}
.chip.valid{background:#EDF0F0;color:#4A5A5E}
.chip.fixable{background:#FDF1DC;color:#8A5A0E;border-color:#F2CE8E}
.chip.contestable{background:#DDF0EC;color:#0B5248;border-color:#9FD3C8}
.chip.needs_review{background:#fff;color:#5E6E72;border:1px dashed #9AA8AB}
.chip.ok{background:#E3F3E8;color:#1E6B3A}
.chip.seed{background:#F3EEF9;color:#5B3E86}
.quote{border-left:3px solid var(--teal);padding:.35rem .8rem;background:#F7FAF9;color:#26393D;font-size:.95rem;margin:.35rem 0}
.quote.reg{border-left-color:var(--mari)}
.small{color:var(--slate);font-size:.86rem}
.trace{font-size:.9rem;border-left:2px solid var(--line);padding-left:.8rem;margin:.15rem 0}
.trace b{color:var(--teal)}
.risk{font-weight:700;padding:.15rem .6rem;border-radius:6px}
.risk.low{background:#E3F3E8;color:#1E6B3A}.risk.medium{background:#FDF1DC;color:#8A5A0E}.risk.high{background:#F8E1DC;color:#8E2F1F}
@media (prefers-reduced-motion: reduce){*{transition:none !important;animation:none !important}}
</style>
"""

STATUS_LABEL = {"valid": "Valid cut", "fixable": "Fixable now", "contestable": "Contestable", "needs_review": "Advisory"}


def esc(s) -> str:
    return html.escape(str(s or ""))


def chip(status: str, label: str | None = None) -> str:
    return f'<span class="chip {status}">{esc(label or STATUS_LABEL.get(status, status))}</span>'


def hero(rep, cur: str, style: str) -> str:
    M = lambda x: money(x, cur, style)
    c = rep.claimed or 1
    paid = rep.payout_before
    fixed = max(0, rep.payout_after_fixes - rep.payout_before)
    contest = max(0, rep.recoverable_via_appeal)
    rest = max(0, c - paid - fixed - contest)
    seg = lambda v, col: f'<div style="width:{v / c * 100:.2f}%;background:{col}" title="{M(v)}"></div>' if v > 0 else ""
    stripes = "repeating-linear-gradient(135deg,#0F6B5F 0 6px,#3F9C8F 6px 12px)"
    appeal = rep.mode == "appeal"
    left_label = "Insurer paid" if appeal else "Insurer pays as-is"
    right_val = rep.payout_after_appeal if appeal else rep.payout_after_fixes
    right_label = "Correct payout after appeal" if appeal else "After ClaimShield fixes"
    gain = right_val - paid
    return f"""
<div class="cs-hero">
 <div class="cs-nums">
  <div class="cs-num"><div class="v">{M(rep.claimed)}</div><div class="l">Claimed</div></div>
  <div class="cs-num"><div class="v" style="color:var(--cut)">{M(paid)}</div><div class="l">{left_label}</div></div>
  <div class="cs-arrow">→</div>
  <div class="cs-num big"><div class="v">{M(right_val)}</div><div class="l">{right_label} <b style="color:var(--teal)">(+{M(gain)})</b></div></div>
  {'' if appeal or contest <= 0 else f'<div class="cs-num"><div class="v" style="color:var(--teal)">{M(contest)}</div><div class="l">Further contestable</div></div>'}
  <div class="cs-num"><div class="v"><span class="risk {rep.risk_band}">{rep.risk_score}</span></div><div class="l">Claim risk ({rep.risk_band})</div></div>
 </div>
 <div class="cs-bar">{seg(paid, '#10262B')}{seg(fixed, '#E9A23B')}{seg(contest, stripes)}{seg(rest, '#E7C3BA')}</div>
 <div class="cs-legend"><span style="--c:#10262B">Paid as-is {M(paid)}</span><span style="--c:#E9A23B">Recovered by fixes {M(fixed)}</span>
 <span style="--c:#0F6B5F">Contestable {M(contest)}</span><span style="--c:#E7C3BA">Your share {M(rest)}</span></div>
</div>"""

EXTRA_CSS = """
<style>
.cs-step{display:flex;gap:.4rem;margin:.6rem 0 1rem;flex-wrap:wrap;line-height:1.6}
.cs-step span{padding:.3rem .85rem;border-radius:999px;background:#EAF0EE;color:#5E6E72;font-size:.9rem;white-space:nowrap;display:inline-block}
.cs-step span.on{background:#0F6B5F;color:#fff;font-weight:600}
.cs-step span.done{background:#CFE5DF;color:#0B5248}
.cs-verdict{border-radius:14px;padding:1.1rem 1.3rem;margin:.2rem 0 .8rem;background:#10262B;color:#fff}
.cs-verdict .big{font-size:1.55rem;font-weight:700;line-height:1.25}
.cs-verdict .gain{color:#F3C26B}
.cs-verdict .sm{opacity:.85;margin-top:.35rem}
.cs-card{border:1px solid #DCE3E1;border-radius:12px;padding:.8rem 1rem;background:#fff;height:100%}
.cs-card h4{margin:.1rem 0 .3rem;color:#10262B}
.cs-card p{color:#5E6E72;font-size:.92rem;margin:0}
.cs-act{border-left:4px solid #E9A23B;background:#fff;padding:.5rem .8rem;margin:.35rem 0;border-radius:6px}
.cs-act.contestable{border-left-color:#0F6B5F}.cs-act.needs_review{border-left-color:#9AA8AB}
.cs-act .who{color:#5E6E72;font-size:.85rem}
div.stButton > button{border-radius:10px;min-height:2.6rem}
.cs-h1{font-size:2.5rem;line-height:1.12;font-weight:700;color:#10262B;margin:.4rem 0 .5rem;max-width:22ch}
.cs-lead{font-size:1.1rem;color:#3D4F53;max-width:62ch;margin-bottom:.8rem}
.cs-trust{display:flex;flex-wrap:wrap;gap:.6rem;margin:.6rem 0 1.2rem}
.cs-trust span{background:#fff;border:1px solid #DCE3E1;border-radius:8px;padding:.35rem .7rem;font-size:.9rem;color:#26393D}
.cs-trust b{color:#0F6B5F}
.cs-chips{display:flex;flex-wrap:wrap;gap:.3rem;margin:.25rem 0 .4rem}
.cs-chips span{font-size:.78rem;padding:.08rem .5rem;border-radius:999px;background:#EEF3F1;color:#3D5155}
.cs-chips span.lang{background:#FDF1DC;color:#7A4E0B}
.cs-headline{font-weight:700;color:#0F6B5F;font-size:1.02rem}
.cs-snap{display:inline-block;background:#E3F3E8;color:#1E6B3A;border-radius:8px;padding:.2rem .6rem;font-size:.85rem;margin-bottom:.4rem}
.cs-stepnum{font-weight:700;color:#E9A23B;font-size:1.3rem}
.cs-gauge{margin:.2rem 0 .6rem}.cs-gauge-track{height:10px;border-radius:999px;background:linear-gradient(90deg,#E3F3E8,#FDF1DC,#F8E1DC);overflow:hidden}
.cs-gauge-fill{height:100%;border-radius:999px}.cs-gauge-lbl{font-size:.86rem;color:#5E6E72;margin-top:.2rem}
.cs-big{font-size:1.9rem;font-weight:700;color:#0F6B5F;line-height:1.1}
.cs-kpi{border:1px solid #DCE3E1;border-radius:12px;background:#fff;padding:.7rem .9rem}
.cs-kpi .l{color:#5E6E72;font-size:.85rem}.cs-kpi .v{font-size:1.55rem;font-weight:700;line-height:1.2}
.cs-kpi.gain .v{color:#0F6B5F}.cs-kpi.contest .v{color:#0B5248}.cs-kpi.share .v{color:#8E2F1F}
.cs-tbl{width:100%;border-collapse:collapse;font-size:.95rem;margin:.3rem 0 .2rem}
.cs-tbl th{text-align:left;color:#5E6E72;font-weight:600;font-size:.82rem;border-bottom:1px solid #DCE3E1;padding:.35rem .5rem}
.cs-tbl td{border-bottom:1px solid #EEF2F0;padding:.5rem;vertical-align:top}
.cs-tbl td.n{color:#5E6E72;width:2rem}.cs-tbl td.amt{text-align:right;font-weight:700;color:#0F6B5F;white-space:nowrap}
.cs-tbl th.amt{text-align:right}.cs-tbl tr.total td{border-top:2px solid #10262B;border-bottom:none;font-weight:700}
.cs-tbl .who{color:#5E6E72;font-size:.82rem}
.cs-rec{background:#fff;border:1px solid #DCE3E1;border-left:4px solid #9AA8AB;border-radius:8px;padding:.5rem .8rem;margin:.35rem 0}
.cs-rec .why{color:#5E6E72;font-size:.85rem}
</style>
"""


def stepper(labels: list[str], active: int) -> str:
    out = []
    for i, l in enumerate(labels):
        cls = "on" if i == active else "done" if i < active else ""
        out.append(f'<span class="{cls}">{i + 1}. {esc(l)}</span>')
    return '<div class="cs-step">' + "".join(out) + "</div>"


def verdict(rep, M) -> str:
    if rep.mode == "appeal":
        big = (f"The insurer paid <b>{M(rep.payout_before)}</b>. We found <span class='gain'>{M(rep.recoverable_via_appeal)}</span> you can claim back."
               if rep.recoverable_via_appeal > 0 else f"The insurer's decision looks consistent with your policy ({M(rep.payout_before)} paid).")
    elif not rep.findings:
        big = f"Looks clean: the insurer should pay about <b>{M(rep.payout_before)}</b> of {M(rep.claimed)}."
    else:
        gain = rep.payout_after_fixes - rep.payout_before
        big = (f"As it stands the insurer will likely pay <b>{M(rep.payout_before)}</b> of {M(rep.claimed)}."
               + (f" Fix {sum(1 for f in rep.findings if f.status == 'fixable')} thing(s) now to get <span class='gain'>{M(gain)} more</span>." if gain > 0 else ""))
    return f'<div class="cs-verdict"><div class="big">{big}</div><div class="sm">Claim risk {rep.risk_score}/100 ({rep.risk_band}) · {len(rep.findings)} finding(s)</div></div>'


def action(a, M) -> str:
    return (f'<div class="cs-act {esc(a["status"])}"><b>{esc(a["action"])}</b><div class="who">{esc(a["who"])} · {esc(a["when"])}'
            f'{" · " + M(a["impact"]) if a["impact"] else ""}</div></div>')


def money_bar(rep, M) -> str:
    """Where every rupee of the claim goes: paid as-is · recovered by fixes · contestable · your share."""
    c = rep.claimed or 1
    paid = rep.payout_before
    fixed = max(0, rep.payout_after_fixes - rep.payout_before)
    contest = max(0, rep.recoverable_via_appeal)
    rest = max(0, c - paid - fixed - contest)
    seg = lambda v, col: f'<div style="width:{v / c * 100:.2f}%;background:{col}" title="{M(v)}"></div>' if v > 0 else ""
    stripes = "repeating-linear-gradient(135deg,#0F6B5F 0 6px,#3F9C8F 6px 12px)"
    return (f'<div class="cs-bar">{seg(paid, "#10262B")}{seg(fixed, "#E9A23B")}{seg(contest, stripes)}{seg(rest, "#E7C3BA")}</div>'
            f'<div class="cs-legend"><span style="--c:#10262B">Paid as-is {M(paid)}</span><span style="--c:#E9A23B">Recovered by fixes {M(fixed)}</span>'
            f'<span style="--c:#0F6B5F">Contestable {M(contest)}</span><span style="--c:#E7C3BA">Your share {M(rest)}</span></div>')


def risk_gauge(score: int, band: str) -> str:
    col = {"low": "#2E8B57", "medium": "#E9A23B", "high": "#B8442F"}.get(band, "#5E6E72")
    return (f'<div class="cs-gauge"><div class="cs-gauge-track"><div class="cs-gauge-fill" style="width:{max(3, min(100, score))}%;background:{col}"></div></div>'
            f'<div class="cs-gauge-lbl"><b style="color:{col}">{score}/100</b> claim risk · {band}</div></div>')
