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


def test_note_to_material_leaves_alt_title_for_grok():
    """Per the agreed design, alt_title is SEMANTIC (Grok's job), NOT deterministic. The
    deterministic material leaves it None; the dedup/resolver key falls back to the mention
    verbatim so null is safe."""
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
    assert m["citation"]["alt_title"] is None


def test_grok_enrich_alt_title_expands_and_syncs_both_levels():
    """Grok expands green-book shorthand into a full alt_title and writes it to BOTH the
    top-level and citation.alt_title in lockstep (no drift). Idempotent."""
    from src.extraction.supplemental_advanced import _enrich_alt_title

    class _G:
        def chat_completion(self, **k):
            return "Letter, Craig to Office of the Chief of Military History, 20 April 1956"

    data = {
        "alt_title": None,
        "citation": {"title": "Unknown", "alt_title": None},
        "mentions": [{"verbatim_reference": "See Ltr, Craig to OCMH, 20 Apr 56."}],
    }
    assert _enrich_alt_title(data, data["citation"], {}, _G()) is True
    assert data["alt_title"] == data["citation"]["alt_title"]
    assert "Letter" in data["alt_title"] and "Ltr" not in data["alt_title"]
    # idempotent: already expanded -> no change
    assert _enrich_alt_title(data, data["citation"], {}, _G()) is False
    # no grok client -> fail-safe no change
    d2 = {
        "alt_title": None,
        "citation": {"alt_title": None},
        "mentions": [{"verbatim_reference": "Ltr x."}],
    }
    assert _enrich_alt_title(d2, d2["citation"], {}, None) is False


def test_new_bare_stub_forms_skipped():
    """idem/id./supra/passim/q.v./ante are bare back-references -> skipped like ibid."""
    from src.extraction.bibliography import _is_stub_citation

    for s in ["Idem.", "Id.", "Supra.", "Passim.", "q.v.", "Ante."]:
        assert _is_stub_citation(s) is True, s


def test_chapter_backref_merges_short_title_repeat(tmp_path):
    """User's canonical cases combined:
    - note 9 cites THREE documents ('Irwin Diary; Ltr, Gen Walker...; 5th Div G-3 Jnl...')
      -> splits into 3 records (semicolon delimiter).
    - note 17 = 'Irwin Diary.' -> MERGES into note 9's Irwin Diary record (2 mentions).
    - note 16 'Ibid., p. 47' -> merges into note 15's MS # B-810.
    So 4 distinct documents total: Irwin Diary (2 mentions), Ltr Gen Walker, 5th Div G-3
    Jnl, MS # B-810 (2 mentions)."""
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
    assert len(files) == 4, f"expected 4 distinct records, got {len(files)}"
    recs = [json.load(open(f)) for f in files]
    # the Irwin Diary entry merged notes 9 and 17 (short-title repeat)
    irwin = next(
        r
        for r in recs
        if any(
            "Irwin Diary" in (m.get("verbatim_reference") or "") for m in r["mentions"]
        )
    )
    refs = sorted(m.get("reference_number") for m in irwin["mentions"])
    assert refs == ["17", "9"], refs
    # MS # B-810 merged note 15 + the 'Ibid., p. 47' of note 16
    ms = next(
        r
        for r in recs
        if any("B-810" in (m.get("verbatim_reference") or "") for m in r["mentions"])
    )
    assert len(ms["mentions"]) == 2, ms["mentions"]


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


def test_expand_multidoc_notes_splits_on_semicolon_not_comma():
    """Green-book footnotes separate documents with ';' (comma is intra-citation). A
    multi-doc note splits into one sub-note per document (number suffixed 3 -> 3b); a
    single document with internal commas is NOT split."""
    from src.extraction.bibliography import _expand_multidoc_notes

    out = _expand_multidoc_notes(
        [
            {
                "number": 3,
                "url": "http://x/#fn3",
                "_ref_type": "footnote",
                "text": "Interv with Thorson, former G-3, FUSA, 12 Sep 56; Sylvan Diary, passim.",
            }
        ]
    )
    assert [n["number"] for n in out] == [3, "3b"]
    assert out[0]["text"].startswith("Interv with Thorson")
    assert out[1]["text"] == "Sylvan Diary, passim."
    # a single citation with internal commas must pass through unchanged (NOT split)
    single = _expand_multidoc_notes(
        [{"number": 4, "text": "MS # B-810, Schmidt, Kaempfe im Rheinland."}]
    )
    assert len(single) == 1 and single[0]["number"] == 4


def test_expand_multidoc_delimiter_is_configurable():
    """The delimiter is corpus-scoped (green-book ';' default); other corpora may override.
    Comma is NOT a universal separator."""
    from src.extraction.bibliography import _expand_multidoc_notes

    # default (;): comma does not split
    assert len(_expand_multidoc_notes([{"number": 1, "text": "A, B, C."}])) == 1
    # explicit override for a hypothetical comma-delimited corpus
    assert (
        len(_expand_multidoc_notes([{"number": 1, "text": "A, B, C."}], delimiter=","))
        == 3
    )


def test_alt_title_consistency_validation_blocks_drift(tmp_path):
    """JSON validation: a bibliography record whose top-level alt_title disagrees with
    citation.alt_title must be BLOCKED (the two copies must never drift)."""
    from src.utils.file_lock import write_json_with_lock

    bib = tmp_path / "bibliography"
    bib.mkdir()
    base = {
        "BibliographyID": "01JQKV6Z9F0000000000000000",
        "title": "Unknown",
        "mentions": [],
    }
    # consistent -> allowed
    ok = dict(
        base,
        alt_title="Letter X",
        citation={"title": "Unknown", "alt_title": "Letter X"},
    )
    assert write_json_with_lock(bib / "ok.json", ok, entity="bibliography") is True
    # drifted -> blocked
    bad = dict(
        base, alt_title="Letter X", citation={"title": "Unknown", "alt_title": "Ltr X"}
    )
    assert write_json_with_lock(bib / "bad.json", bad, entity="bibliography") is False


def test_enrich_bib_file_wires_alt_title_expansion(tmp_path):
    """End-to-end wiring guard: _enrich_bib_file (the Phase-3 step) must actually RUN the
    alt_title expansion over a deterministic record and persist the expanded, level-synced
    result. Protects against the expansion existing but not being wired to execute."""
    import json
    from src.extraction.supplemental_advanced import _enrich_bib_file

    bib = tmp_path / "bibliography"
    bib.mkdir()
    rec = {
        "BibliographyID": "01JQKV6Z9F0000000000000001",
        "title": "Unknown",
        "alt_title": None,
        "citation": {"title": "Unknown", "alt_title": None},
        "mentions": [{"verbatim_reference": "See Ltr, Craig to OCMH, 20 Apr 56."}],
    }
    f = bib / "rec.json"
    f.write_text(json.dumps(rec))

    class _G:
        def chat_completion(self, **k):
            return "Letter, Craig to Office of the Chief of Military History, 20 April 1956"

    changed = _enrich_bib_file(f, {}, _G())
    assert changed is True
    out = json.loads(f.read_text())
    assert out["alt_title"] == out["citation"]["alt_title"]
    assert "Letter" in out["alt_title"] and "Ltr" not in out["alt_title"]


def test_deterministic_bib_filename_stable_and_keyed():
    """Same citation -> same deterministic filename (concurrency-safe: concurrent tasks
    writing the same citation target ONE file, not two random-ULID files)."""
    from src.extraction.bibliography import _deterministic_bib_filename, _match_keys

    k1 = _match_keys(
        {"citation": {"title": "Unknown"}}, "See Interv with Collins, 25 Jan 54"
    )
    k2 = _match_keys(
        {"citation": {"title": "Unknown"}}, "See Interv with Collins, 25 Jan 54"
    )
    assert _deterministic_bib_filename(k1) == _deterministic_bib_filename(k2)
    assert _deterministic_bib_filename(k1).startswith(
        "bib_"
    ) and _deterministic_bib_filename(k1).endswith(".json")
    other = _match_keys({"citation": {"title": "Unknown"}}, "MS # B-810, Schmidt")
    assert _deterministic_bib_filename(other) != _deterministic_bib_filename(k1)


def test_reconcile_merges_duplicate_records(tmp_path):
    """Option B rebuild-reconcile: records sharing a match key (a legacy random-ULID file +
    the deterministic file) collapse into ONE canonical bib_<hash>.json, unioning mentions
    and carrying enriched fields. S3-native, no Dynamo."""
    import glob
    import json
    from src.extraction.bibliography import (
        reconcile_bibliography_dir,
        _deterministic_bib_filename,
        _match_keys,
    )

    bib = tmp_path / "bibliography"
    bib.mkdir()
    verb = "See Interv with Collins, 25 Jan 54."
    canon = _deterministic_bib_filename(
        _match_keys({"citation": {"title": "Unknown"}}, verb)
    )
    # deterministic file (no alt_title) + legacy random-ULID dup (enriched alt_title), same citation
    (bib / canon).write_text(
        json.dumps(
            {
                "BibliographyID": "01AAA",
                "title": "Unknown",
                "alt_title": None,
                "resource_urls": [],
                "mentions": [
                    {
                        "MentionID": "01JQKV6Z9F0000000000000001",
                        "book": "R",
                        "chapter": "c2",
                        "reference_number": "3",
                        "verbatim_reference": verb,
                    }
                ],
            }
        )
    )
    (bib / "interv_01LEGACY.json").write_text(
        json.dumps(
            {
                "BibliographyID": "01BBB",
                "title": "Unknown",
                "alt_title": "Interview with Collins",
                "resource_urls": ["https://catalog.archives.gov/id/1"],
                "mentions": [
                    {
                        "MentionID": "01JQKV6Z9F0000000000000002",
                        "book": "R",
                        "chapter": "c9",
                        "reference_number": "99",
                        "verbatim_reference": verb,
                    }
                ],
            }
        )
    )
    merged_away = reconcile_bibliography_dir(bib)
    files = [f for f in glob.glob(str(bib / "*.json")) if "index" not in f]
    assert merged_away == 1
    assert len(files) == 1 and files[0].endswith(canon)
    rec = json.loads(open(files[0]).read())
    assert len(rec["mentions"]) == 2  # unioned
    assert rec["alt_title"] == "Interview with Collins"  # enriched field carried
    assert rec["resource_urls"] == ["https://catalog.archives.gov/id/1"]


def _material(verb, title="Unknown", reference_number=""):
    return {
        "verbatim_reference": verb,
        "reference_number": reference_number,
        "citation": {"title": title},
    }


def test_write_produces_shard_without_prepopulated_dir(tmp_path, monkeypatch):
    """Scoped-read write path: with NO pre-downloaded dir, store_bibliography_entry writes a
    per-writer shard bib_<hash>__<writer>.json (never needs the whole dir)."""
    import re
    import src.extraction.bibliography as bib

    bib._WRITER_ID = None
    monkeypatch.setenv("ECS_TASK_ID", "taskA")
    bib_dir = tmp_path / "bibliography"  # does NOT exist yet
    bib.store_bibliography_entry(bib_dir, _material("V Corps Ops, p. 256."), "B", "c1")
    files = [f.name for f in bib_dir.glob("*.json")]
    assert len(files) == 1
    assert re.match(r"^bib_[0-9a-f]{16}__taskA\.json$", files[0]), files[0]


def test_same_citation_one_writer_unions_into_one_shard(tmp_path, monkeypatch):
    """Two mentions of the SAME citation by ONE writer land in ONE shard with 2 mentions."""
    import src.extraction.bibliography as bib

    bib._WRITER_ID = None
    monkeypatch.setenv("ECS_TASK_ID", "taskA")
    bib_dir = tmp_path / "bibliography"
    verb = "Ltr, Hodges to Gerow, 11 Sep 44."
    bib.store_bibliography_entry(
        bib_dir, _material(verb, reference_number="1"), "B", "c1"
    )
    bib.store_bibliography_entry(
        bib_dir, _material(verb, reference_number="2"), "B", "c2"
    )
    files = list(bib_dir.glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert len(rec["mentions"]) == 2


def test_reconcile_collapses_shards_from_two_writers(tmp_path, monkeypatch):
    """CONCURRENCY CRUX: two different writers (tasks) each write their own shard for the SAME
    citation (no clobber). Full-corpus reconcile collapses both shards into ONE canonical
    bib_<hash>.json, unioning mentions across writers."""
    import glob
    import src.extraction.bibliography as bib
    from src.extraction.bibliography import reconcile_bibliography_dir

    bib_dir = tmp_path / "bibliography"
    verb = "Interv with Collins, 25 Jan 54."

    # writer A
    bib._WRITER_ID = None
    monkeypatch.setenv("ECS_TASK_ID", "taskA")
    bib.store_bibliography_entry(
        bib_dir, _material(verb, reference_number="1"), "B", "c1"
    )
    # writer B (distinct id) — must NOT clobber A's shard
    bib._WRITER_ID = None
    monkeypatch.setenv("ECS_TASK_ID", "taskB")
    bib.store_bibliography_entry(
        bib_dir, _material(verb, reference_number="2"), "B", "c9"
    )

    shards = [f for f in bib_dir.glob("*.json") if "__" in f.name]
    assert len(shards) == 2, "each writer must have its own shard (no clobber)"

    merged_away = reconcile_bibliography_dir(bib_dir)
    files = [f for f in glob.glob(str(bib_dir / "*.json")) if "index" not in f]
    assert merged_away == 2  # two shards folded into one canonical file
    assert len(files) == 1
    assert files[0].endswith(".json") and "__" not in Path(files[0]).name  # canonical
    rec = json.loads(open(files[0]).read())
    assert len(rec["mentions"]) == 2  # mentions unioned across writers


def test_reconcile_fuzzy_collapses_near_titles(tmp_path):
    """reconcile folds near-title duplicates (>=0.85) that exact-hash grouping misses."""
    import glob
    from src.extraction.bibliography import reconcile_bibliography_dir

    bib_dir = tmp_path / "bibliography"
    bib_dir.mkdir()
    base = {
        "resource_urls": [],
    }
    (bib_dir / "p1_01A.json").write_text(
        json.dumps(
            {
                **base,
                "BibliographyID": "01A",
                "title": "The Siegfried Line Campaign",
                "mentions": [
                    {
                        "MentionID": "01JQKV6Z9F0000000000000001",
                        "book": "B",
                        "reference_number": "1",
                    }
                ],
            }
        )
    )
    (bib_dir / "p2_01B.json").write_text(
        json.dumps(
            {
                **base,
                "BibliographyID": "01B",
                "title": "The Siegfried Line Campain",  # typo -> fuzzy match
                "mentions": [
                    {
                        "MentionID": "01JQKV6Z9F0000000000000002",
                        "book": "B",
                        "reference_number": "2",
                    }
                ],
            }
        )
    )
    merged_away = reconcile_bibliography_dir(bib_dir)
    files = [f for f in glob.glob(str(bib_dir / "*.json")) if "index" not in f]
    # canonical bib_<hash>.json differs from both p1_/p2_ names -> both unlinked
    assert merged_away == 2 and len(files) == 1
    rec = json.loads(open(files[0]).read())
    assert len(rec["mentions"]) == 2  # unioned across the near-title duplicate
