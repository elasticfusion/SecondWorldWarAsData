"""Grok content-verdict: fail-closed, false-lead, fact-confirmed vs opinion, qualification."""

from unittest.mock import MagicMock

import src.utils.url_content_verdict as cv


def _grok(payload):
    g = MagicMock()
    g.extract_json.return_value = payload
    return g


def test_fail_closed_no_content():
    v = cv.evaluate("", "First US Army", "WWII", _grok({"relevant": True}))
    assert v.relevant is False and not v.writable_claims


def test_fail_closed_no_grok():
    v = cv.evaluate("real content", "X", "WWII", None)
    assert v.relevant is False


def test_fail_closed_on_grok_error():
    g = MagicMock()
    g.extract_json.side_effect = Exception("boom")
    v = cv.evaluate("content", "X", "WWII", g)
    assert v.relevant is False and "grok error" in v.false_lead_reason


def test_false_lead_rejected():
    v = cv.evaluate(
        "content",
        "Paris",
        "WWII",
        _grok(
            {
                "relevant": False,
                "false_lead": True,
                "false_lead_reason": "wrong Paris (Texas)",
            }
        ),
    )
    assert v.false_lead is True and not v.writable_claims


def test_confirmed_fact_writable_unconfirmed_dropped_opinion_tagged():
    g = _grok(
        {
            "relevant": True,
            "false_lead": False,
            "source_type": "primary",
            "proximity": "contemporaneous",
            "claims": [
                {
                    "text": "9th Div crossed the Roer on 23 Feb 1945",
                    "claim_type": "fact",
                    "confirmed": True,
                },
                {"text": "unsupported fact", "claim_type": "fact", "confirmed": False},
                {
                    "text": "the attack was ill-conceived",
                    "claim_type": "opinion",
                    "confirmed": False,
                },
            ],
        }
    )
    v = cv.evaluate(
        "content", "9th Infantry Division", "WWII", g, source_url="https://nara.gov/x"
    )
    texts = {c.text for c in v.writable_claims}
    assert "9th Div crossed the Roer on 23 Feb 1945" in texts  # confirmed fact -> write
    assert "unsupported fact" not in texts  # unconfirmed fact -> dropped
    assert "the attack was ill-conceived" in texts  # opinion -> written tagged
    assert v.source_qualification["source_type"] == "primary"
    assert v.source_qualification["proximity"] == "contemporaneous"


def test_uncited_web_qualifies_low():
    g = _grok(
        {
            "relevant": True,
            "false_lead": False,
            "source_type": "tertiary",
            "evidentiary_support": "none",
            "claims": [],
        }
    )
    v = cv.evaluate("content", "X", "WWII", g)
    sq = v.source_qualification
    assert sq["source_type"] == "tertiary" and sq["evidentiary_support"] == "none"
    assert sq["proximity"] == "n/a"  # proximity only meaningful for primary


def test_bad_enum_coerced_to_safe():
    g = _grok(
        {
            "relevant": True,
            "false_lead": False,
            "source_type": "bogus",
            "proximity": "nonsense",
            "claims": [],
        }
    )
    v = cv.evaluate("content", "X", "WWII", g)
    assert v.source_qualification["source_type"] == "unknown"
