"""PR5: _find_page_number must use a monotonic cursor so a paragraph whose leading text
repeats an earlier phrase resolves to its OWN (sequential) position/page, not the first
occurrence (the old content.find-from-0 bug mis-assigned page_number, which flows to the DB).
"""

from __future__ import annotations

from pathlib import Path

from src.parser import _build_page_map, _create_paragraphs, split_into_blocks


def test_repeated_prefix_resolves_to_correct_page():
    # Two paragraphs sharing a >60-char leading prefix, on different pages (page 1 vs page 2).
    shared = "The regiment advanced toward the river under heavy fire that morning"
    content = (
        '<a id="page1"></a>\n\n'
        f"{shared}, reaching the near bank by noon.\n\n"
        '<a id="page2"></a>\n\n'
        f"{shared}, and then withdrew after dark."
    )
    page_map = _build_page_map(content)
    blocks = split_into_blocks(content)
    paras = _create_paragraphs(blocks, 0, "full", Path("c.md"), content, page_map)

    assert len(paras) == 2
    # First paragraph -> page 1; second (same prefix) -> page 2 (NOT page 1).
    assert paras[0].page_number == 1, paras[0].page_number
    assert paras[1].page_number == 2, (
        f"repeated-prefix paragraph mis-assigned to page {paras[1].page_number} "
        "(content.find-from-0 bug: resolved to the FIRST occurrence)"
    )
