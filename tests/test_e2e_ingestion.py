"""Combined end-to-end test: Phase 0 -> Phase 1 on local fixtures.

Drives the real phase entry points (phase0_ingest.main / phase1_parse.main)
against a temporary content tree with structure repair enabled, and asserts the
final on-disk artifacts carry the wired features end to end:

* Phase 0 converts a docx source to markdown in ``ocr_output/`` (requirement #6).
* Phase 1 parses a chapter whose markdown contains a Chandra-style block quote
  and a flattened task-org table, and the written ``*-parsed.json`` preserves
  the quotation (``is_quote`` + ``quote_attribution``) and the table hints.

Hermetic: no AWS, no network, no GPU (the Chandra OCR leg is a separate cloud
step and is not exercised here). docx conversion is skipped cleanly when pandoc
is absent, but the Phase 1 assertions always run.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict

import pytest

import phase0_ingest
import phase1_parse
from src.utils import config as config_mod

_HAVE_PANDOC = shutil.which("pandoc") is not None

# A chapter-content markdown as Chandra would emit it: a block quote rendered as
# plain paragraphs (no '>'), and a flattened 2-D task-org table (no <table>).
CHAPTER_MD = """\
The Division held the line under heavy fire.

In the words of Mr. Ralph Ingersoll's controversial book, Top Secret: (pp. 270-271)

"Bastogne and St. Vith were the road centers the Germans had to have.

"What newspapers did not tell was what happened back of St. Vith."

PROOF ASSIGNMENTS

170300 - March South

CC-A
40
48
A/33

CC-B
31
23
B/33

DIV TMS
77
129
446
"""

CHAPTER_META = """\
series: United States Army in World War II
book: St Vith
author: Donald P. Boyer
chapter_title: The Action
license: public_domain
"""


def _build_content_tree(root: Path) -> None:
    """Create a content tree: one book/chapter for Phase 1, one docx for Phase 0."""
    book = root / "StVith"
    ch = book / "chapter1"
    ch.mkdir(parents=True)
    (ch / "chapter1-content.md").write_text(CHAPTER_MD, encoding="utf-8")
    (ch / "chapter1-meta.yaml").write_text(CHAPTER_META, encoding="utf-8")

    # A docx source for the Phase 0 document-conversion pre-step.
    if _HAVE_PANDOC:
        src_md = book / "src.md"
        src_md.write_text("# Report\n\nText about **St. Vith**.\n", encoding="utf-8")
        subprocess.run(
            ["pandoc", str(src_md), "-o", str(book / "annex.docx")],
            check=True,
            capture_output=True,
        )
        src_md.unlink()


def _fixture_config() -> Dict:
    """Minimal valid config; paths are filled in relative to the tmp base_dir."""
    return {
        "paths": {
            "content_root": "contentrepository",
            "output_root": "output",
            "content_output": "output/content",
        },
        "api": {"grok": {"model": "grok-4"}, "calls_per_minute": 30},
        "ingestion": {
            "convert_documents": True,
            "repair_markdown_structure": True,
        },
        "logging": {"console": False},
    }


def _patch_phases(monkeypatch, base_dir: Path) -> None:
    """Point both phase entry points at the tmp tree via config/paths."""
    cfg = _fixture_config()

    def fake_load_config(_path=None):
        return cfg

    def fake_get_paths(_config, _base=None):
        return {k: base_dir / v for k, v in cfg["paths"].items()}

    # Both modules import these names directly.
    monkeypatch.setattr(phase0_ingest, "load_config", fake_load_config)
    monkeypatch.setattr(phase0_ingest, "get_paths", fake_get_paths)
    monkeypatch.setattr(phase1_parse, "load_config", fake_load_config)
    monkeypatch.setattr(phase1_parse, "get_paths", fake_get_paths)
    # phase1 imports get_content_root lazily from the config module.
    monkeypatch.setattr(
        config_mod,
        "get_content_root",
        lambda paths: paths["content_output"],
    )


def test_phase0_to_phase1_end_to_end(tmp_path, monkeypatch) -> None:
    base = tmp_path
    content_root = base / "contentrepository"
    content_root.mkdir()
    _build_content_tree(content_root)
    _patch_phases(monkeypatch, base)

    # --- Phase 0: document conversion pre-step (docx -> ocr_output markdown) ---
    phase0_ingest.main()

    if _HAVE_PANDOC:
        converted = content_root / "StVith" / "ocr_output" / "annex.md"
        assert converted.exists(), "Phase 0 did not convert the docx source"
        assert "St. Vith" in converted.read_text(encoding="utf-8").replace("\xa0", " ")

    # --- Phase 1: parse chapter with structure repair enabled ---
    phase1_parse.main()

    # A single content file has section_id="" -> "full" suffix in the filename.
    parsed_path = base / "output" / "content" / "StVith" / "chapter1full-parsed.json"
    assert parsed_path.exists(), "Phase 1 did not write parsed output"
    doc = json.loads(parsed_path.read_text(encoding="utf-8"))

    paragraphs = doc["paragraphs"]
    quotes = [p for p in paragraphs if p.get("is_quote")]
    # Both quotation paragraphs preserved as quotes (not flattened to prose).
    assert len(quotes) == 2, f"expected 2 quote paragraphs, got {len(quotes)}"
    assert all("Ingersoll" in p.get("quote_attribution", "") for p in quotes)
    assert any("Bastogne and St. Vith" in p["text"] for p in quotes)

    # Flattened task-org table captured as review-flagged hints.
    hints = doc.get("table_hints", [])
    assert hints, "expected table_hints from the flattened task-org table"
    assert hints[0]["needs_review"] is True
    labels = [s["label"] for s in hints[0]["snapshots"]]
    assert "170300" in labels
    groups = {g["group"] for s in hints[0]["snapshots"] for g in s["groups"]}
    assert any(gr.startswith("CC-A") for gr in groups)


@pytest.mark.skipif(_HAVE_PANDOC, reason="covers the no-pandoc path")
def test_phase1_repair_runs_without_pandoc(tmp_path, monkeypatch) -> None:
    # Even without pandoc (no docx conversion), Phase 1 structure repair still
    # produces the quote/table artifacts.
    base = tmp_path
    content_root = base / "contentrepository"
    content_root.mkdir()
    _build_content_tree(content_root)
    _patch_phases(monkeypatch, base)

    phase0_ingest.main()
    phase1_parse.main()

    doc = json.loads(
        (
            base / "output" / "content" / "StVith" / "chapter1full-parsed.json"
        ).read_text()
    )
    assert sum(1 for p in doc["paragraphs"] if p.get("is_quote")) == 2
