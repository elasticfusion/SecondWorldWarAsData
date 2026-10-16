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
    source_urls = []
    for f in _bib_files(out):
        d = json.loads(f.read_text())
        # resource_urls stays EMPTY (filled by Phase-3 resolution); the parsed source
        # URL is provenance on the mention.
        assert d.get("resource_urls") == []
        for m in d.get("mentions") or []:
            if m.get("source_url"):
                source_urls.append(m["source_url"])
    assert "javascript:alert(1)" not in source_urls  # non-http dropped
    assert "https://x/#fn2" in source_urls  # http(s) kept as provenance


def test_materialize_local_mode_noop(tmp_path, monkeypatch):
    """In local-file mode (no entity store) materialize is a no-op — records are
    already on disk; returns 0 without error."""
    import src.extraction.bibliography as bib

    monkeypatch.setattr(
        "src.utils.entity_store.get_entity_store", lambda: None, raising=False
    )
    assert bib.materialize_bibliography_to_local(tmp_path / "out") == 0


def test_ibiblio_source_is_public_domain(tmp_path):
    """Owner rule: anything derived from ibiblio/hyperwar (US Army CMH green books) is a
    US Government work -> public domain, set deterministically at parse time."""
    out = tmp_path / "out"
    pf = _write_parsed(
        out,
        "footnotes",
        [
            {
                "number": 1,
                "url": "https://www.ibiblio.org/hyperwar/USA/x.html#fn1",
                "text": "Interv, Collins.",
            },
            {"number": 2, "url": "https://example.com/x", "text": "Non-gov source."},
        ],
    )
    extract_bibliography_deterministic(pf, out, book="RoerDams", chapter="chapter3a")
    by_url = {}
    for f in _bib_files(out):
        d = json.loads(f.read_text())
        src = ((d.get("mentions") or [{}])[0]).get("source_url", "")
        by_url[src] = d
    pub = next(d for u, d in by_url.items() if "ibiblio" in u)
    assert pub["license"] == "public_domain"
    assert (pub.get("copyright_status") or {}).get("status") == "public_domain"
    assert pub.get("resource_urls") == []  # left empty for Phase 3 resolution
    other = next(d for u, d in by_url.items() if "example.com" in u)
    assert other["license"] == "unknown"  # left for Phase 3 resolution


def test_derive_alt_title_strips_but_does_not_expand():
    """alt_title is a MINIMAL non-semantic baseline: strip leading 'See'/trailing period,
    but do NOT regex-expand abbreviations (that semantic work is the LLM's job — the Green
    Books' abbreviation vocabulary is too large/idiosyncratic for a static map).
    """
    from src.extraction.bibliography import _derive_alt_title

    assert (
        _derive_alt_title("See Interv with Collins, 25 Jan 54.")
        == "Interv with Collins, 25 Jan 54"
    )
    assert (
        _derive_alt_title("Cf. Ltr, Craig to OCMH, 20 Apr 56.")
        == "Ltr, Craig to OCMH, 20 Apr 56"
    )
    assert _derive_alt_title("MS # B-810, Schmidt.") == "MS # B-810, Schmidt"
    assert _derive_alt_title("") == ""


def test_note_to_material_sets_alt_title():
    """The deterministic material must carry a non-null alt_title (not just title=Unknown)."""
    from src.extraction.bibliography import _note_to_material

    m = _note_to_material(
        {
            "text": "See Interv with Collins, 25 Jan 54.",
            "number": 1,
            "_ref_type": "endnote",
        }
    )
    assert m is not None
    assert m["citation"]["title"] == "Unknown"
    assert m["citation"]["alt_title"] == "Interv with Collins, 25 Jan 54"


def test_new_bare_stub_forms_skipped():
    """idem/id./supra/passim/q.v./ante are bare back-references -> skipped like ibid."""
    from src.extraction.bibliography import _is_stub_citation

    for s in ["Idem.", "Id.", "Supra.", "Passim.", "q.v.", "Ante."]:
        assert _is_stub_citation(s) is True, s


def test_chapter_backref_merges_short_title_repeat(tmp_path):
    """User's canonical case: note 9 cites 'Irwin Diary; ...'; note 17 = 'Irwin Diary.'
    The short-title repeat must MERGE into note 9's entry (2 mentions), not create a 2nd
    record. 'Ibid., p. 47' must merge into the immediately-preceding full citation."""
    from src.extraction.bibliography import _store_notes
    import glob
    import json

    notes = [
        {
            "number": 9,
            "url": "http://x/#fn9",
            "text": "Irwin Diary; Ltr, Gen Walker to Hist Div, 8 Oct 47; 5th Div G-3 Jnl, 9 Nov 44.",
        },
        {"number": 15, "url": "http://x/#fn15", "text": "MS # B-810, Schmidt."},
        {"number": 16, "url": "http://x/#fn16", "text": "Ibid., p. 47."},
        {"number": 17, "url": "http://x/#fn17", "text": "Irwin Diary."},
    ]
    bib = tmp_path / "bibliography"
    _store_notes(notes, bib, book="RoerDams", chapter="chapter5")
    files = [f for f in glob.glob(str(bib / "*.json")) if "index" not in f]
    assert len(files) == 2, f"expected 2 distinct records, got {len(files)}"
    recs = [json.load(open(f)) for f in files]
    by_mentions = {len(r.get("mentions", [])): r for r in recs}
    # both entries carry exactly 2 mentions (the full + its back-reference)
    assert sorted(len(r.get("mentions", [])) for r in recs) == [2, 2]
    # the Irwin Diary entry merged notes 9 and 17
    irwin = next(
        r
        for r in recs
        if any(
            "Irwin Diary" in (m.get("verbatim_reference") or "") for m in r["mentions"]
        )
    )
    refs = sorted(m.get("reference_number") for m in irwin["mentions"])
    assert refs == ["17", "9"], refs


def test_first_in_chapter_citation_is_not_a_backref(tmp_path):
    """Edge case (user): note 9's 'Irwin Diary; ...' is the FIRST reference to the Irwin
    Diary document in this chapter (even though 'General Irwin' the person appears in
    earlier chapters). It must anchor as a NEW full citation, not resolve as a back-ref.
    The resolver keys on the document short-title within THIS chapter, never on a person
    or on other chapters."""
    from src.extraction.bibliography import _resolve_back_reference

    n9 = (
        "Irwin Diary; Ltr, Gen Walker to Hist Div, 8 Oct 47; 5th Div G-3 Jnl, 9 Nov 44."
    )
    # empty chapter table => first occurrence => NOT a back-reference
    assert _resolve_back_reference(n9, []) is None
    # a person-only antecedent (different short-title) must not trigger a merge
    works = [{"title": "Patton Papers", "short": "patton papers", "author": "patton"}]
    assert _resolve_back_reference("Irwin Diary.", works) is None
