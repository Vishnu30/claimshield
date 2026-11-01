"""Offline tests for the source collector parsers (no network)."""
import base64
from core.sources import parse_ddg, parse_bing, score, probe_months


def test_parse_ddg():
    html = '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Firdai.gov.in%2Fdoc%2Fmaster.pdf&rut=x">Master <b>Circular</b></a>'
    assert parse_ddg(html) == [("https://irdai.gov.in/doc/master.pdf", "Master Circular")]


def test_parse_bing():
    enc = base64.urlsafe_b64encode(b"https://www.starhealth.in/arogya.pdf").decode().rstrip("=")
    html = f'<h2><a href="https://www.bing.com/ck/a?!&amp;p=1&amp;u=a1{enc}&amp;ntb=1">Arogya Sanjeevani</a></h2>'
    assert parse_bing(html)[0][0] == "https://www.starhealth.in/arogya.pdf"


def test_score_prefers_official_pdf():
    a = score("https://www.starhealth.in/x/arogya-sanjeevani.pdf", "Arogya", ["arogya"], ["starhealth.in"])
    b = score("https://www.scribd.com/doc/arogya.pdf", "Arogya", ["arogya"], ["starhealth.in"])
    assert a > b and a >= 11


def test_probe_pattern():
    url, tag = next(probe_months("https://www.cioins.co.in/AwardsMonthwise/{Mon}{YYYY}/Health_individual {Month} {YYYY}.pdf"))
    assert "AwardsMonthwise/" in url and "Health_individual" in url and len(tag) == 7


def test_discover_adds_only_new_health_wordings(monkeypatch):
    import core.sources as s
    fake = [("https://www.starhealth.in/sites/default/files/young-star-policy-wording.pdf", "Young Star Policy Wording"),
            ("https://www.starhealth.in/sites/default/files/motor-policy-wording.pdf", "Motor"),
            ("https://www.starhealth.in/known.pdf", "Policy wording"),
            ("https://example.com/random.pdf", "Policy")]
    monkeypatch.setattr(s, "ENGINES", (lambda q: fake,))
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    new = s.discover({"https://www.starhealth.in/known.pdf"}, set(), insurers=["Star Health"])
    assert [r["urls"] for r in new] == ["https://www.starhealth.in/sites/default/files/young-star-policy-wording.pdf"]
    assert new[0]["id"].startswith("auto_star_health_")


def test_not_found_is_remembered():
    import core.sources as s
    from datetime import datetime, timezone
    st = {"x": {"status": "not_found", "at": datetime.now(timezone.utc).isoformat()}}
    assert s.recently_failed(st, "x") and not s.recently_failed(st, "y")
