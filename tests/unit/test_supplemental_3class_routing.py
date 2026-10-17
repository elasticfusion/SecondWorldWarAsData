"""3-class supplemental routing: citation -> bibliography, prose -> Phase-2 notes-event,
HYBRID -> BOTH. Owner directive 2026-10-16."""

from unittest.mock import patch

from src.extraction.supplemental import _route_by_content_class


def _sub(materials):
    return [
        {
            "EventID": "",
            "Sub-eventID": "",
            "Sub-event_Name": "S",
            "Supplemental_Material": materials,
        }
    ]


def _route(materials, tmp_path):
    with patch(
        "src.extraction.bibliography.store_bibliography_entry", return_value="01BIB"
    ) as store:
        factual, ambiguous, bib_count = _route_by_content_class(
            _sub(materials), tmp_path, "RoerDams", "chapter3a"
        )
    return factual, ambiguous, bib_count, store


def test_citation_routes_to_bibliography_only(tmp_path):
    f, a, n, store = _route(
        [
            {
                "content_class": "citation",
                "citation": {"title": "Cross-Channel Attack", "author": ["Harrison"]},
                "verbatim_reference": "Harrison, Cross-Channel Attack.",
            }
        ],
        tmp_path,
    )
    assert n == 1 and store.call_count == 1  # -> bibliography
    assert f == []  # NOT a notes-event


def test_prose_routes_to_notes_event_only(tmp_path):
    f, a, n, store = _route(
        [
            {
                "content_class": "prose",
                "prose_content": "Casualties were understated in early reports.",
                "citation": None,
            }
        ],
        tmp_path,
    )
    assert n == 0 and store.call_count == 0  # NOT stored as a bibliography record
    assert len(f) == 1  # -> notes-event
    assert f[0]["verbatim_reference"] == "Casualties were understated in early reports."


def test_hybrid_produces_BOTH(tmp_path):
    """A hybrid note -> one bibliography record AND one notes-event item."""
    f, a, n, store = _route(
        [
            {
                "content_class": "hybrid",
                "prose_content": "Casualties were understated in early reports.",
                "citation": {"title": "AAR, 9th Inf Div, 15 Nov 44, FUSA G-1 file"},
                "verbatim_reference": "Casualties...; see AAR, 9th Inf Div, 15 Nov 44, FUSA G-1 file.",
            }
        ],
        tmp_path,
    )
    assert n == 1 and store.call_count == 1  # citation half -> bibliography
    assert len(f) == 1  # prose half -> notes-event
    assert f[0]["verbatim_reference"] == "Casualties were understated in early reports."


def test_legacy_classes_still_route(tmp_path):
    f, a, n, _ = _route(
        [
            {
                "content_class": "document_reference",
                "citation": {"title": "X Report"},
                "verbatim_reference": "X Report.",
            },
            {
                "content_class": "factual_content",
                "prose_content": "Narrative.",
                "citation": None,
            },
        ],
        tmp_path,
    )
    assert n == 1  # document_reference -> bib
    assert len(f) == 1  # factual_content -> notes-event


def test_llm_classification_field_drives_routing_endtoend(tmp_path):
    """BLOCKER fix: the LLM emits 'classification' (not 'content_class'). sanitize must map
    it so a HYBRID's prose becomes an event AND its citation becomes a citation — end to end.
    """
    from src.extraction.supplemental import sanitize_supplemental_data

    data = sanitize_supplemental_data(
        {
            "EventID": "01E",
            "Sub-eventID": "01S",
            "Supplemental_Material": [
                {
                    "classification": "hybrid",  # LLM field name (no content_class)
                    "prose_content": "Casualties were understated in early reports.",
                    "citation": {"title": "AAR, 9th Inf Div, 15 Nov 44, FUSA G-1 file"},
                    "verbatim_reference": "Casualties...; see AAR, 9th Inf Div.",
                    "reference_type": "endnote",
                }
            ],
        }
    )
    mat = data["Supplemental_Material"][0]
    assert (
        mat["content_class"] == "hybrid"
    )  # classification -> content_class reconciled
    f, a, n, store = _route([mat], tmp_path)
    assert n == 1 and len(f) == 1  # hybrid -> BOTH bib AND notes-event


def test_no_silent_drop_bare_cross_reference(tmp_path):
    """A bare cross-reference (empty citation + empty prose) must go to review, not vanish."""
    f, a, n, store = _route(
        [
            {
                "content_class": "citation",
                "citation": {"title": "Unknown"},  # no real document
                "verbatim_reference": "",
                "prose_content": "",
            }
        ],
        tmp_path,
    )
    assert n == 0 and len(f) == 0  # routed to neither bib nor notes
    assert len(a) == 1  # ...so it's queued for human review (not dropped)
