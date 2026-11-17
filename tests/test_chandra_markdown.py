"""Chandra OCR markdown of one chapter, normalised into the form Phase 1 parses: notes gathered
into a Footnotes section, paragraphs rejoined across page breaks, model-written image text
removed, page anchors kept."""

from collections import Counter
from pathlib import Path

from src.ingestion.chandra_markdown import normalise_chandra_chapter

P = '<a id="page{}"></a>'


def _page(n, *paras):
    return P.format(n) + "\n\n" + "\n\n".join(paras) + "\n"


CHAPTER = (
    _page(
        288,
        "## CHAPTER VIII",
        "# The Sixth of June",
        "On 8 May General Eisenhower set D Day.<sup>1</sup>",
        "<sup>1</sup> Information on mounting.",
        "The troops were mar-",
        "---",
        "<sup>2</sup> The latter was the principal area of",
    )
    + _page(
        289,
        "shaling at Slapton Sands, and the assault went in.<sup>2</sup>",
        "---",
        "installations, as planned.",
    )
    + _page(
        290,
        "![Black and white photograph of soldiers.](abc_1_img.webp)A photograph showing soldiers.",
        "**PREINVASION SCENES.** *Soldiers in mess line.*",
    )
)


def _run(text=CHAPTER, vocab=None):
    return normalise_chandra_chapter(text, vocab)


def test_notes_are_gathered_into_one_footnotes_section_in_the_parser_form():
    md, log = _run()
    body, notes = md.split("### Footnotes")
    assert "<sup>1</sup> Information" not in body
    assert '\\[<a href="#cn1" id="fn1">1</a>\\] Information on mounting.' in notes
    assert '<sup><a href="#fn1" id="cn1">1</a></sup>' in body
    assert (
        log.counts["note"] == 2 and not log.unmatched_markers and not log.unused_notes
    )


def test_a_note_run_over_onto_the_next_page_is_continued_after_the_rule():
    md, log = _run()
    assert (
        '\\[<a href="#cn2" id="fn2">2</a>\\] The latter was the principal area of installations, as planned.'
        in md
    )
    assert log.counts["note_continued"] == 1


def test_a_paragraph_cut_by_a_page_break_is_rejoined_with_the_anchor_inline():
    md, log = _run()
    assert 'The troops were mar<a id="page289"></a>shaling at Slapton Sands' in md
    assert log.counts["paragraph_rejoined"] == 1 and log.counts["hyphen_removed"] == 1


def test_a_hyphen_the_book_prints_is_kept_at_a_break():
    text = _page(1, "the counter-", "---") + _page(2, "attack began.")
    md, log = _run(text, Counter({"counter-attack": 5, "counterattack": 1}))
    assert 'the counter-<a id="page2"></a>attack began.' in md
    assert log.counts["hyphen_kept"] == 1


def test_the_model_written_image_text_is_removed_and_the_figure_listed():
    md, log = _run()
    assert "A photograph showing soldiers" not in md
    assert "**PREINVASION SCENES.**" in md  # the book's own caption stays
    assert [(f.page, f.file) for f in log.figures] == [(290, "abc_1_img.webp")]


def test_every_page_anchor_survives():
    md, _ = _run()
    for n in (288, 289, 290):
        assert P.format(n) in md


def test_a_notes_further_paragraph_between_two_notes_stays_with_its_note():
    text = _page(
        1,
        "Body text.<sup>65</sup><sup>66</sup>",
        "<sup>65</sup> Gerow was graduated in 1911.",
        "General Huebner enlisted in 1910.",
        "<sup>66</sup> See above.",
    )
    md, _ = _run(text)
    assert "Gerow was graduated in 1911. General Huebner enlisted in 1910." in md
    assert "\n\nGeneral Huebner" not in md


def test_narrative_after_a_pages_notes_stays_narrative():
    text = _page(
        1,
        "Talley reported.<sup>77</sup>",
        "<sup>77</sup> Talley Notes.",
        "Already, however, the threat grew.",
        "The gravest threat came at 0725.",
    )
    md, _ = _run(text)
    body = md.split("### Footnotes")[0]
    assert "Already, however, the threat grew." in body


def test_a_repeated_note_number_keeps_its_first_definition():
    text = _page(1, "x.<sup>3</sup>", "<sup>3</sup> First.", "<sup>3</sup> Second.")
    md, log = _run(text)
    assert "First." in md and "Second." not in md and log.duplicate_notes == [3]


def test_markers_without_a_note_are_reported_and_left():
    md, log = _run(_page(1, "Text.<sup>9</sup>"))
    assert "<sup>9</sup>" in md and log.unmatched_markers == [9]
    assert "### Footnotes" not in md


def test_the_parser_reads_the_notes_pages_and_paragraphs(tmp_path: Path):
    from src.ingestion.chapter_meta import build_meta_yaml
    from src.parser import parse_content_file, parse_metadata

    md, _ = _run()
    folder = tmp_path / "B" / "chapter8"
    folder.mkdir(parents=True)
    (folder / "chapter8-content.md").write_text(md, encoding="utf-8")
    meta = folder / "chapter8-meta.yaml"
    meta.write_bytes(
        build_meta_yaml("B", source_url="", source_url_basis="ocr", source_key="k")
    )
    doc = parse_content_file(
        folder / "chapter8-content.md", "s", 1, parse_metadata(meta)
    )
    assert [f.number for f in doc.footnotes] == [1, 2]
    texts = [p.text for p in doc.paragraphs]
    assert not any(t.startswith(("<sup>", "\\[", "![")) for t in texts)
    joined = next(p for p in doc.paragraphs if p.text.startswith("The troops were"))
    assert (joined.page_number, joined.page_end) == (288, 289)
    assert {m.page_number for m in doc.page_markers} == {288, 289, 290}


def test_a_map_plates_legend_and_labels_are_not_narrative():
    text = _page(
        287,
        "![A historical map of Europe titled 'SITUATION IN EUROPE'.](m_1_img.webp)",
        "**SITUATION IN EUROPE**  \n6 June 1944",
        "- AXIS-DOMINATED AREA\n- AREA UNDER ALLIED CONTROL",
        "Labels on map: "
        + ", ".join(["NORWAY", "SWEDEN", "FINLAND", "GREAT BRITAIN"] * 10),
    ) + _page(288, "The narrative goes on here as before, a full sentence of the book.")
    md, log = _run(text)
    assert "NORWAY" not in md and "AXIS-DOMINATED" not in md
    assert "The narrative goes on" in md
    assert log.figures[0].printed_title == "SITUATION IN EUROPE   6 June 1944"
    assert log.counts["map_text_removed"] == 3


def test_a_page_with_a_map_and_prose_keeps_its_prose():
    prose = (
        "The division moved forward along the road and the enemy withdrew to the river "
        * 3
    )
    md, log = _run(
        _page(1, "![A map of the sector.](m.webp)", "SECTOR MAP", prose.strip() + ".")
    )
    assert (
        "SECTOR MAP" in md
        and "The division moved" in md
        and not log.counts["map_text_removed"]
    )


# --- review findings: narrative must never be moved into a note or dropped silently ---------------


def test_a_note_ending_without_a_stop_does_not_swallow_later_narrative():
    text = _page(1, "Body.<sup>5</sup>", "<sup>5</sup> Ibid., p. 45") + _page(
        4, "de Gaulle refused to agree to the plan as drawn."
    )
    md, log = _run(text)
    body, notes = md.split("### Footnotes")
    assert "de Gaulle refused" in body and "de Gaulle" not in notes
    assert log.counts["lowercase_unjoined"] == 1


def test_a_note_cut_at_the_foot_of_a_page_continues_at_the_foot_of_the_next():
    """Chandra sets the run-over after the next page's narrative, below the rule (CXA note 36)."""
    text = _page(1, "Body.<sup>5</sup>", "<sup>5</sup> See the report of the") + _page(
        2,
        "The narrative of the next page goes on, and ends complete.",
        "---",
        "chief of staff, 4 May.",
    )
    md, _ = _run(text)
    body, notes = md.split("### Footnotes")
    assert "See the report of the chief of staff, 4 May." in notes
    assert "chief of staff" not in body


def test_a_repeated_note_definition_and_its_continuation_are_dropped_but_logged():
    text = _page(
        1,
        "x.<sup>3</sup>",
        "<sup>3</sup> First.",
        "<sup>3</sup> Second repeated text.",
        "A paragraph that follows the repeated definition.",
        "<sup>4</sup> Next.",
    )
    md, log = _run(text)
    notes = md.split("### Footnotes")[1]
    assert "First." in notes and "Second" not in notes
    assert [d["kind"] for d in log.dropped] == [
        "repeated_note",
        "repeated_note_continuation",
    ]
    assert log.dropped[0]["text"] == "Second repeated text."
    assert log.dropped[0]["number"] == 3
    assert log.dropped[1]["text"] == "A paragraph that follows the repeated definition."
    assert "follows the repeated definition" not in md  # not leaked into the narrative


def test_a_picture_that_merely_shows_a_map_is_not_a_map_plate():
    text = _page(
        1,
        "![Soldiers studying a map on a jeep.](j.webp)",
        "The patrol left at dawn.",
        "## Aftermath",
        "<sup>1</sup> A note.",
    )
    md, log = _run(text)
    assert "The patrol left at dawn." in md and "## Aftermath" in md
    assert not log.counts["map_text_removed"]


def test_a_map_plate_never_loses_a_heading_or_a_note():
    text = _page(
        5,
        "![A map of the Cotentin peninsula.](m.webp)",
        "LA FIERE BRIDGEHEAD",
        "Labels on map: " + ", ".join(["ST. LO", "CARENTAN"] * 5),
        "## CHAPTER IX",
        "<sup>1</sup> A note on the map.",
    )
    md, log = _run(text)
    assert "## CHAPTER IX" in md and "A note on the map." in md
    assert "Labels on map" not in md
    assert [d["kind"] for d in log.dropped].count("map_text") == 2


def test_a_short_complete_sentence_on_a_map_page_keeps_the_page():
    text = _page(
        5,
        "![A map of the sector.](m.webp)",
        "The attack was held up at the river by German artillery fire.",
    )
    md, log = _run(text)
    assert "held up at the river" in md and not log.counts["map_text_removed"]


def test_removed_picture_descriptions_are_kept_in_the_log():
    _, log = _run()
    kinds = [d["kind"] for d in log.dropped]
    assert kinds == ["image_description"]
    assert "A photograph showing soldiers" in log.dropped[0]["text"]
    assert log.to_dict()["dropped"] == log.dropped


def test_notes_between_notes_are_counted_for_audit():
    text = _page(
        1,
        "Body.<sup>65</sup><sup>66</sup>",
        "<sup>65</sup> Gerow was graduated in 1911.",
        "General Huebner enlisted in 1910.",
        "<sup>66</sup> See above.",
    )
    _, log = _run(text)
    assert log.counts["note_paragraph_between_notes"] == 1


def test_the_continuation_of_a_repeated_note_is_not_joined_to_the_narrative_before_it():
    text = _page(
        1,
        "The unit advanced to the river and held it until",
        "<sup>3</sup> First.",
        "<sup>3</sup> Second repeated.",
        "continued in the repeated note.",
    )
    md, log = _run(text)
    assert "until continued" not in md
    assert [d["kind"] for d in log.dropped][-1] == "repeated_note_continuation"


def test_a_new_page_of_narrative_ends_the_dropping_of_a_repeated_note():
    text = _page(
        1, "x.<sup>3</sup>", "<sup>3</sup> First.", "<sup>3</sup> Again."
    ) + _page(2, "The narrative of the next page is kept.")
    md, _ = _run(text)
    assert "The narrative of the next page is kept." in md.split("### Footnotes")[0]


def test_ordinary_text_after_a_note_closes_it():
    text = _page(5, "Body.<sup>5</sup>", "<sup>5</sup> Ibid., p. 45") + _page(
        6,
        "A complete paragraph of the narrative on the next page.",
        "stray lower-case fragment.",
    )
    md, _ = _run(text)
    assert "stray lower-case fragment" not in md.split("### Footnotes")[1]
