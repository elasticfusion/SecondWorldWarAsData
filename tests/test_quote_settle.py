"""A quote is in the source or it is not (src/utils/quote_match). Calibrated on the USA-E-XChannel
Phase-2 provenance report (2026-11-15): of 63 quote findings, most were in the book after all - a
bare note marker the model copied ("the war.[3]"), a sentence a page break or a misplaced
footnote split across two paragraphs, or an OCR / one-word slip - and were attributed to the
wrong sub-event because attribution and validation matched quotes differently."""

import json
from pathlib import Path

from src.extraction import supplemental as supp
from src.utils import quote_match as Q


def test_a_bare_note_marker_is_not_part_of_the_quote():
    source = "Hitler believed that he had won the war.<sup>[3](https://x/#fn3)</sup> He"
    index = [(6, Q.normalize(source))]
    assert Q.locate("Hitler believed that he had won the war.[3]", index) == (
        [6],
        Q.EXACT,
    )
    assert Q.normalize("eighteen Commandos[37] for") == "eighteen commandos for"
    # an editorial bracket is the text's own words, never a marker
    assert Q.normalize("[Krancke reported on 4 June]") == "krancke reported on 4 june"


def test_a_quote_runs_from_inside_one_paragraph_into_the_start_of_the_next():
    index = [
        (77, Q.normalize("given the shadow. The")),
        (78, Q.normalize("Combined Chiefs of Staff made him responsible.")),
        (79, Q.normalize("Combined effort.")),
    ]
    assert Q.locate("The Combined Chiefs of Staff made him", index) == (
        [77, 78],
        Q.EXACT,
    )
    # the tail must START the next paragraph: no stitching of two arbitrary places
    assert Q.locate("the shadow effort", index) == ([], "")
    assert Q.in_paragraphs("The Combined Chiefs", index) == (77, Q.EXACT)


def test_a_spliced_quote_is_not_bridged_across_two_paragraphs():
    """A quote's head must END its paragraph, or both sides be long (a note set inside it)."""
    index = [
        (1, Q.normalize("General Marshall spoke at length about the plan and left.")),
        (2, Q.normalize("Patton said he would go.")),
    ]
    assert Q.locate("General Patton said he would go", index) == ([], "")
    assert Q.locate("Marshall Patton said he would go", index) == ([], "")
    assert Q.locate("the plan and left. Patton said he would go", index) == (
        [1, 2],
        Q.EXACT,
    )


def test_sentences_split_after_a_word_and_rejoin_after_an_abbreviation():
    text = (
        "The Second World War. He did so. Lt. Gen. Bradley went to St. Lo. See pp. 12-14. "
        '"Go," he said. 1944 began. it went on.'
    )
    assert Q._pieces(text) == [
        "The Second World War.",
        "He did so.",
        "Lt. Gen. Bradley went to St. Lo.",
        "See pp. 12-14.",
        '"Go," he said.',
        "1944 began. it went on.",  # a lower-case piece continues the sentence
    ]


def test_a_year_in_brackets_is_text_and_an_empty_quote_is_unquoted():
    from src.extraction import windows as W

    assert Q.normalize("the order [1944] stood") == "the order 1944 stood"
    text = {"Paragraph_001": "Rain fell."}
    for empty in (None, "   ", "...", "\u2026"):
        assert W.settle(empty, "", text) == ("", [1], W.UNQUOTED)
    assert Q.source_passage("Rain fell", "Rain fell.") is None  # too short to repair


def test_source_passage_is_the_shortest_run_of_sentences_that_holds_the_quote():
    text = (
        "There was talk. In the next few days the rapid collapse of General Schlieben's "
        "forces in the Continent caused Hitler to flirt with the notion. Later, nothing."
    )
    quote = (
        "the rapid collapse of General Schlieben's forces in the Cotentin caused Hitler"
    )
    assert Q.source_passage(quote, text) == (
        "In the next few days the rapid collapse of General Schlieben's forces in the "
        "Continent caused Hitler to flirt with the notion."
    )
    assert Q.source_passage("COSSAC in London", text) is None  # not even nearly


def test_the_stored_passage_is_whole_words_of_its_source_paragraph():
    raw = (
        "The 2d Division (Maj. Gen. Walter M. Robertson),<sup>[45](#fn45)</sup> which had "
        "begun landing the evening of 7 June, had *enough* units ashore. Next sentence."
    )
    passage = Q.source_passage(
        "The 2d Division (Maj. Gen. Walter M. Robertson),[45] which began landing", raw
    )
    assert passage.startswith("The 2d Division (Maj. Gen. Walter M. Robertson), which")
    assert "<sup>" not in passage and "*" not in passage
    assert Q.contains(Q.normalize(raw), Q.normalize(passage))


def test_the_sentence_naming_an_entity_keeps_abbreviations_and_plurals():
    text = "It was Lt. Gen. Omar N. Bradley who went. The LCTs beached at H Hour."
    assert (
        Q.naming_sentence("Omar N. Bradley", text)
        == "It was Lt. Gen. Omar N. Bradley who went."
    )
    assert Q.naming_sentence("LCT", text) == "The LCTs beached at H Hour."
    assert Q.naming_sentence("Patton", text) is None
    assert Q.naming_sentence("", text) is None


# --- supplemental: each citation's note, deterministically ---------------------------------------
def _parsed(tmp_path: Path) -> Path:
    path = tmp_path / "c-parsed.json"
    path.write_text(
        json.dumps(
            {
                "paragraphs": [],
                "footnotes": [
                    {
                        "number": 4,
                        "text": "FUSA G-3 Journal, 6 Jun 44.",
                        "kind": "footnote",
                    },
                    {
                        "number": 5,
                        "text": "Interv, Col. Smith, 1945; see n. 4.",
                        "kind": "footnote",
                    },
                    {"number": 6, "text": "Ibid.", "kind": "footnote"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_asked_notes_are_read_back_from_the_requests_key():
    event = {"EventID": "E", "Event_Name": "N"}
    _sid, _prompt, material, _eid = supp._sub_event_prompt(
        event, "S", "", "text", ([7], [4, 5]), {}, window="P001-P003"
    )
    assert supp._asked_notes(material) == [
        ("7", "endnote"),
        ("4", "footnote"),
        ("5", "footnote"),
    ]


def test_a_citation_gets_its_notes_number_kind_and_own_text(tmp_path):
    notes = supp.chapter_notes(_parsed(tmp_path))
    response = {
        "Supplemental_Material": [
            {"content_class": "citation", "citation": {"title": "FUSA G-3 Journal"}},
            {"content_class": "citation", "citation": {"title": "Interv, Col. Smith"}},
            {"content_class": "prose", "prose_content": "Smith said so."},
        ]
    }
    located = supp.locate_notes(response, "E|S|e:|f:4,5", notes)
    a, b, prose = response["Supplemental_Material"]
    assert (a["reference_number"], a["reference_type"]) == ("4", "footnote")
    assert a["verbatim_reference"] == "FUSA G-3 Journal, 6 Jun 44."
    # the note's OWN words, never the cross-reference-resolved text the prompt showed
    assert b["verbatim_reference"] == "Interv, Col. Smith, 1945"  # its part of the note
    # several notes asked and none holds the prose: no guess
    assert "reference_number" not in prose and "verbatim_reference" not in prose
    assert located == (2, 1)


def test_one_asked_note_is_every_items_note_and_prose_gets_no_verbatim(tmp_path):
    notes = supp.chapter_notes(_parsed(tmp_path))
    response = {
        "Supplemental_Material": [
            {"content_class": "prose", "prose_content": "Commentary."},
            {
                "content_class": "citation",
                "citation": {"title": "FUSA G-3 Journal"},
                "reference_type": "bibliography",
            },
        ]
    }
    assert supp.locate_notes(response, "E|S|e:|f:4", notes) == (2, 0)
    prose, cited = response["Supplemental_Material"]
    assert prose["reference_number"] == "4" and not prose.get("verbatim_reference")
    assert (cited["reference_number"], cited["reference_type"]) == ("4", "footnote")
    assert cited["verbatim_reference"] == "FUSA G-3 Journal, 6 Jun 44."


def test_no_verbatim_for_a_stub_note_or_a_citation_naming_no_document(tmp_path):
    """A verbatim_reference alone makes an item a stored document (_has_document): an "Ibid."
    note or an empty citation must not become a bibliography record."""
    notes = supp.chapter_notes(_parsed(tmp_path))
    stub = {"content_class": "citation", "citation": {"title": "Something cited"}}
    empty = {"content_class": "citation", "citation": {"title": None}}
    response = {"Supplemental_Material": [stub, empty]}
    assert supp.locate_notes(response, "E|S|e:|f:6", notes) == (2, 0)
    assert stub["reference_number"] == empty["reference_number"] == "6"
    assert not stub.get("verbatim_reference") and not empty.get("verbatim_reference")


def test_a_note_citing_several_documents_gives_each_its_own_part(tmp_path):
    notes = supp.chapter_notes(_parsed(tmp_path))
    notes["4"][0]["text"] = "FUSA G-3 Journal, 6 Jun 44; Interv, Col. Smith, 1945."
    a = {"content_class": "citation", "citation": {"title": "Interv, Col. Smith"}}
    b = {"content_class": "citation", "citation": {"title": "A history nobody cited"}}
    supp.locate_notes({"Supplemental_Material": [a, b]}, "E|S|e:|f:4", notes)
    assert a["verbatim_reference"] == "Interv, Col. Smith, 1945."
    # no one part holds it: which document it is, is unknown - never all of them
    assert b["reference_number"] == "4" and b["verbatim_reference"] == ""


def test_footnote_and_endnote_of_one_number_asked_together_are_told_apart_by_text(
    tmp_path,
):
    notes = supp.chapter_notes(_parsed(tmp_path))
    notes["4"].append(
        {"number": 4, "text": "Endnote four: AAR, 1st Div.", "_ref_type": "endnote"}
    )
    end = {"content_class": "citation", "citation": {"title": "AAR, 1st Div"}}
    foot = {"content_class": "citation", "citation": {"title": "FUSA G-3 Journal"}}
    neither = {"content_class": "citation", "citation": {"title": "Unrelated memo"}}
    response = {"Supplemental_Material": [end, foot, neither]}
    assert supp.locate_notes(response, "E|S|e:4|f:4", notes) == (2, 1)
    assert (end["reference_type"], end["verbatim_reference"]) == (
        "endnote",
        "Endnote four: AAR, 1st Div.",
    )
    assert (foot["reference_type"], foot["verbatim_reference"]) == (
        "footnote",
        "FUSA G-3 Journal, 6 Jun 44.",
    )
    assert "reference_number" not in neither


def test_a_footnote_and_an_endnote_of_one_number_are_told_apart(tmp_path):
    notes = supp.chapter_notes(_parsed(tmp_path))
    notes["4"].append(
        {"number": 4, "text": "Endnote four: AAR, 1st Div.", "_ref_type": "endnote"}
    )
    item = {"content_class": "citation", "citation": {"title": "AAR, 1st Div"}}
    supp.locate_notes({"Supplemental_Material": [item]}, "E|S|e:4|f:", notes)
    assert (item["reference_type"], item["verbatim_reference"]) == (
        "endnote",
        "Endnote four: AAR, 1st Div.",
    )


def test_a_title_two_asked_notes_hold_is_not_guessed(tmp_path):
    notes = supp.chapter_notes(_parsed(tmp_path))
    response = {
        "Supplemental_Material": [
            {
                "content_class": "citation",
                "citation": {"title": "FUSA G-3 Journal 6 Jun 44"},
            }
        ]
    }
    notes["5"][0]["text"] = "FUSA G-3 Journal, 6 Jun 44, again."
    assert supp.locate_notes(response, "E|S|e:|f:4,5", notes) == (0, 1)
