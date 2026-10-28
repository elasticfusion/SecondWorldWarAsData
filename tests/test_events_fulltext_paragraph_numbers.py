"""Regression: event sub-events use 'paragraph_numbers' (Grok's key). A mismatch silently emptied
Sub-event_fulltext and forced the summary fallback, starving entity extraction (XChannel-8 bug).
"""

from src.extraction import events as EV


def _parsed():
    return {
        "paragraphs": [
            {"absolute_number": n, "text": f"PARA {n} real source text"}
            for n in range(1, 10)
        ]
    }


def test_reconstruct_fulltext_resolves_paragraph_numbers():
    event = {
        "Event": {
            "Sub-events": [
                {
                    "Sub-eventID": "S1",
                    "Sub-event_summary": "short summary",
                    "paragraph_numbers": [3, 5, 7],
                },
            ]
        }
    }
    EV._reconstruct_fulltext(event, _parsed())
    ft = event["Event"]["Sub-events"][0]["Sub-event_fulltext"]
    assert set(ft.keys()) == {"Paragraph_3", "Paragraph_5", "Paragraph_7"}, ft
    assert "summary" not in ft  # must NOT fall back to summary
    assert "real source text" in ft["Paragraph_3"]


def test_backcompat_old_paragraphs_key_still_works():
    event = {
        "Event": {
            "Sub-events": [
                {"Sub-eventID": "S1", "Sub-event_summary": "s", "paragraphs": [2, 4]},
            ]
        }
    }
    EV._reconstruct_fulltext(event, _parsed())
    ft = event["Event"]["Sub-events"][0]["Sub-event_fulltext"]
    assert set(ft.keys()) == {"Paragraph_2", "Paragraph_4"}


def test_summary_fallback_only_when_truly_no_paragraphs():
    event = {
        "Event": {
            "Sub-events": [
                {
                    "Sub-eventID": "S1",
                    "Sub-event_summary": "only summary",
                    "paragraph_numbers": [],
                },
            ]
        }
    }
    EV._reconstruct_fulltext(event, _parsed())
    ft = event["Event"]["Sub-events"][0]["Sub-event_fulltext"]
    assert ft == {"summary": "only summary"}
