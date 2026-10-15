"""Deterministic-primary bibliography pass: records emitted straight from parsed
footnotes/endnotes with NO LLM/batch/cache, idempotent, stubs skipped."""

import json
from pathlib import Path

from src.extraction.bibliography import (
    _is_stub_citation,
    extract_bibliography_deterministic,
)


def _write_parsed(tmp_path: Path, key: str, notes) -> Path:
    book = tmp_path / "content" / "RoerDams"
    book.mkdir(parents=True, exist_ok=True)
    pf = book / "chapter3a-parsed.json"
    pf.write_text(json.dumps({key: notes}))
    return pf


def _bib_files(out: Path):
    d = out / "bibliography"
    return [f for f in d.glob("*.json") if "index" not in f.name] if d.exists() else []


def test_stub_citations_detected():
    for stub in [
        "Ibid.",
        "ibid",
        "See n. 5, above.",
        "op. cit.",
        "cited in n. 24",
        "3",
    ]:
        assert _is_stub_citation(stub), stub
    for real in [
        "V Corps Operations in the ETO, p. 256.",
        "Ltr, Hodges to Gerow, 11 Sep 44, FUSA G-3 file.",
    ]:
        assert not _is_stub_citation(real), real


def test_emits_record_per_citation_and_skips_stubs(tmp_path):
    out = tmp_path / "out"
    notes = [
        {"number": 1, "url": "http://x/#fn1", "text": "V Corps Operations, p. 256."},
        {
            "number": 2,
            "url": "http://x/#fn2",
            "text": "Ltr, Hodges to Gerow, 11 Sep 44.",
        },
        {"number": 3, "url": "http://x/#fn3", "text": "Ibid."},  # stub -> skipped
    ]
    pf = _write_parsed(out, "footnotes", notes)
    stored = extract_bibliography_deterministic(
        pf, out, book="RoerDams", chapter="chapter3a"
    )
    assert stored == 2  # the stub is skipped
    files = _bib_files(out)
    assert len(files) == 2
    rec = json.loads(files[0].read_text())
    # Real citable provenance, no LLM.
    assert rec["mentions"][0]["book"] == "RoerDams"
    assert rec["mentions"][0]["reference_type"] == "footnote"
    assert rec["mentions"][0]["verbatim_reference"]


def test_endnotes_key_supported(tmp_path):
    out = tmp_path / "out"
    pf = _write_parsed(
        out,
        "endnotes",
        [{"number": 7, "url": "http://x/#en7", "text": "MS # B-810, Schmidt."}],
    )
    stored = extract_bibliography_deterministic(
        pf, out, book="RoerDams", chapter="chapter3a"
    )
    assert stored == 1
    rec = json.loads(_bib_files(out)[0].read_text())
    assert rec["mentions"][0]["reference_type"] == "endnote"


def test_idempotent_no_duplicate_files(tmp_path):
    out = tmp_path / "out"
    pf = _write_parsed(
        out,
        "footnotes",
        [{"number": 1, "url": "u", "text": "V Corps Operations, p. 256."}],
    )
    extract_bibliography_deterministic(pf, out, book="RoerDams", chapter="chapter3a")
    before = len(_bib_files(out))
    # Re-run (submit pass + retrieve re-run + reconcile all call it) must not duplicate.
    extract_bibliography_deterministic(pf, out, book="RoerDams", chapter="chapter3a")
    assert len(_bib_files(out)) == before == 1


def test_missing_parsed_file_safe(tmp_path):
    assert (
        extract_bibliography_deterministic(tmp_path / "nope.json", tmp_path, "B", "c")
        == 0
    )


def test_non_http_url_dropped(tmp_path):
    out = tmp_path / "out"
    pf = _write_parsed(
        out,
        "footnotes",
        [
            {"number": 1, "url": "javascript:alert(1)", "text": "V Corps Ops, p. 256."},
            {
                "number": 2,
                "url": "https://x/#fn2",
                "text": "Ltr, Hodges to Gerow, 11 Sep 44.",
            },
        ],
    )
    extract_bibliography_deterministic(pf, out, book="RoerDams", chapter="chapter3a")
    urls = []
    for f in _bib_files(out):
        urls += json.loads(f.read_text()).get("resource_urls", [])
    assert "javascript:alert(1)" not in urls
    assert "https://x/#fn2" in urls


def test_materialize_local_mode_noop(tmp_path, monkeypatch):
    """In local-file mode (no entity store) materialize is a no-op — records are
    already on disk; returns 0 without error."""
    import src.extraction.bibliography as bib

    monkeypatch.setattr(
        "src.utils.entity_store.get_entity_store", lambda: None, raising=False
    )
    assert bib.materialize_bibliography_to_local(tmp_path / "out") == 0
