"""DRY P1.6 guard: the two straight OOB table parsers (command_posts, higher_units) that joined a
row's raw cells verbatim (' | '.join) now share _common.join_cells_verbatim — one source of truth
for the raw_text provenance reconstruction. These tests pin the verbatim contract (blanks + order
preserved) so provenance stays byte-faithful.
"""

from src.ingestion.oob_markdown._common import join_cells_verbatim


def test_join_is_byte_identical_to_old_inline():
    cells = ["12 Jun 44", "Chartres", "", "France"]
    assert join_cells_verbatim(cells) == " | ".join(cells)  # the exact old behavior


def test_join_preserves_blanks_and_order():
    # higher_units documents "blank cells mean unchanged/none" -> blanks MUST survive verbatim.
    assert join_cells_verbatim(["a", "", "b", ""]) == "a |  | b | "
    assert join_cells_verbatim([""]) == ""
    assert join_cells_verbatim([]) == ""
    # order preserved, no normalization/strip
    assert join_cells_verbatim([" X ", "y"]) == " X  | y"


def test_command_posts_raw_text_uses_shared_join():
    # A representative command-post row's raw_text equals the shared verbatim join of its cells.
    import src.ingestion.oob_markdown.command_posts as cp

    texts = ["5 Aug 44", "Avranches", "Normandy", "France"]
    row, _year = cp._post_row_from_cells(
        texts, "1st Infantry Division", "header", "1944", "ch1-content.md"
    )
    assert row is not None
    assert row.raw_text == join_cells_verbatim(texts)


def test_higher_units_raw_text_uses_shared_join():
    import src.ingestion.oob_markdown.higher_units as hu

    cells = ["6 Jun 44", "V Corps", "First Army", "", "12th AG", ""]
    row = hu._row_from_cells(cells, "1st Infantry Division", "header", "ch1-content.md")
    assert row is not None
    assert row.raw_text == join_cells_verbatim(cells)  # blanks preserved verbatim
