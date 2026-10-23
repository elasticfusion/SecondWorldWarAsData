"""Test the broadened (non-NARA) bibliography OpenSERP archive search."""

from unittest.mock import MagicMock, patch

import src.enrichment.bibliography_resolver as br


def test_search_iterates_non_nara_repositories():
    issued = []

    def fake_get(method, u, params=None, timeout=None, **k):
        issued.append(params["text"])
        r = MagicMock()
        r.status_code = 200
        # a hit only on the HathiTrust (non-NARA) query -> proves multi-repo iteration
        if "HathiTrust" in params["text"]:
            r.json.return_value = {
                "results": [
                    {
                        "url": "http://hathitrust.org/doc",
                        "title": "The Lorraine Campaign",
                    }
                ]
            }
        else:
            r.json.return_value = {"results": []}
        return r

    br._openserp_down = False
    with (
        patch(
            "src.enrichment.bibliography_resolver.get_session",
            return_value=MagicMock(request=fake_get),
        ),
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
        patch("time.sleep"),
        patch.object(br, "_verify_openserp_match", return_value=True),
    ):
        entry = {"citation": {"title": "The Lorraine Campaign", "author": "Cole"}}
        url = br._search_openserp_archive(
            "The Lorraine Campaign", "http://x", grok_client=object(), entry=entry
        )

    assert url == "http://hathitrust.org/doc"
    # broadened beyond a single 'digitized document' query -> multiple repository-targeted ones
    assert len(issued) >= 2
    assert any("HathiTrust" in q for q in issued)


def test_collect_verified_candidates_filters_and_caps():
    """Shared NARA-style helper: verify each candidate, keep verified ones, cap at max_findings."""
    cands = [
        ("https://archive.org/details/a", "Match One"),
        ("https://archive.org/details/b", "Irrelevant"),
        ("https://archive.org/details/c", "Match Two"),
        ("https://archive.org/details/d", "Match Three"),
    ]

    def verify(query, title, url, grok_client, entry=None):
        return "Match" in title

    with patch.object(br, "_verify_openserp_match", side_effect=verify):
        out = br._collect_verified_candidates(
            "q", cands, grok_client=object(), max_findings=2
        )
    # 'Irrelevant' rejected; capped at 2 even though 3 match
    assert out == [
        "https://archive.org/details/a",
        "https://archive.org/details/c",
    ]


def test_collect_verified_candidates_no_grok_accepts_all():
    cands = [
        ("https://archive.org/details/a", "x"),
        ("https://archive.org/details/b", "y"),
    ]
    out = br._collect_verified_candidates("q", cands, grok_client=None, max_findings=5)
    assert out == ["https://archive.org/details/a", "https://archive.org/details/b"]


def test_search_online_sources_aggregates_multiple_findings():
    """multiple findings per entry is OK: archive.org + gutenberg verified hits BOTH land in
    resource_urls + source_metadata.findings (not first-source short-circuit).

    Also proves DECOUPLING: with use_openserp=False, archive.org AND gutenberg still run
    (they are independent of OpenSERP) and both produce findings."""
    entry = {"citation": {"title": "The Lorraine Campaign", "author": "Cole"}}
    config = {
        "nara_api_key": "",  # NARA off
        "use_openserp": False,  # OpenSERP OFF — archive.org + gutenberg must still run
        "search_gutenberg": True,
        "search_archive_org": True,
    }
    with (
        patch.object(br, "_is_nara_searchable", return_value=False),
        patch.object(br, "_search_openserp_archive", return_value=None),
        patch(
            "src.extraction.supplemental_search.search_archive_org_candidates",
            return_value=[("https://archive.org/details/a", "The Lorraine Campaign")],
        ),
        patch(
            "src.extraction.supplemental_search.search_gutenberg_candidates",
            return_value=[("https://gutenberg.org/ebooks/1", "Lorraine")],
        ),
        patch.object(br, "_verify_openserp_match", return_value=True),
    ):
        primary = br._search_online_sources(
            entry,
            "The Lorraine Campaign",
            "",
            "The Lorraine Campaign",
            object(),
            config,
        )

    assert primary == ("https://archive.org/details/a", "archive_org")
    assert entry["resource_urls"] == [
        "https://archive.org/details/a",
        "https://gutenberg.org/ebooks/1",
    ]
    findings = entry["source_metadata"]["findings"]
    assert {f["source"] for f in findings} == {"archive_org", "gutenberg"}


def test_unknown_title_feeds_verbatim_not_placeholder_to_sources():
    """Deterministic records have title='Unknown'; archive.org/Gutenberg must receive the
    alt_title/verbatim citation text, NOT the 'Unknown' placeholder (which their length guard
    would reject -> empty candidates -> sources never really queried)."""
    captured = {}

    def fake_archive(t, a=None):
        captured["archive_title"] = t
        return []

    def fake_gutenberg(t, a=None):
        captured["gutenberg_title"] = t
        return []

    entry = {
        "citation": {"title": "Unknown", "alt_title": "The Lorraine Campaign (Cole)"},
    }
    config = {"nara_api_key": "", "search_archive_org": True, "search_gutenberg": True}
    with (
        patch.object(br, "_is_nara_searchable", return_value=False),
        patch(
            "src.extraction.supplemental_search.search_archive_org_candidates",
            side_effect=fake_archive,
        ),
        patch(
            "src.extraction.supplemental_search.search_gutenberg_candidates",
            side_effect=fake_gutenberg,
        ),
    ):
        br._search_online_sources(
            entry, "Ltr, Cole to OCMH, 1954", "", "Unknown", None, config
        )

    # alt_title preferred over the 'Unknown' placeholder
    assert captured["archive_title"] == "The Lorraine Campaign (Cole)"
    assert captured["gutenberg_title"] == "The Lorraine Campaign (Cole)"


def test_token_gate_rejects_irrelevant_candidates_without_grok_call():
    """Perf-regression guard: _collect_verified_candidates must reject candidates sharing NO
    meaningful token with the query via the cheap deterministic gate, WITHOUT calling the Grok
    verifier (the per-result Grok-call storm on junk web results made Phase 3 take hours).
    """
    calls = {"n": 0}

    def verify(query, title, url, grok_client, entry=None):
        calls["n"] += 1
        return True

    cands = [
        ("https://x/1", "The Unknown : Flammarion, Camille : Free Download"),
        ("https://x/2", "Address Unknown"),
        ("https://x/3", "The Nine Unknown"),
        ("https://x/4", "V Corps Operations in the ETO"),  # real -> should reach grok
    ]
    with patch.object(br, "_verify_openserp_match", side_effect=verify):
        out = br._collect_verified_candidates(
            "V Corps Operations in the European Theater of Operations, p. 256",
            cands,
            grok_client=object(),
        )
    # Only the relevant candidate reached the Grok verifier (3 junk ones gated out for free).
    assert calls["n"] == 1
    assert out == ["https://x/4"]


def test_openserp_verify_budget_caps_grok_calls():
    """Perf guard: OpenSERP caps total Grok verify attempts per citation (<=5) across all query
    templates, so topically-relevant-but-wrong candidates can't accumulate dozens of 3s calls.
    """
    calls = {"n": 0}

    def verify(query, title, url, grok_client, entry=None):
        calls["n"] += 1
        return False  # all rejected -> exhausts the budget rather than returning early

    def fake_get(method, u, params=None, timeout=None, **k):
        r = MagicMock()
        r.status_code = 200
        # 5 topically-relevant results (share 'corps/operations' -> pass token-gate) per query
        r.json.return_value = {
            "results": [
                {"url": f"http://x/{i}", "title": f"V Corps Operations variant {i}"}
                for i in range(5)
            ]
        }
        return r

    br._openserp_down = False
    with (
        patch(
            "src.enrichment.bibliography_resolver.get_session",
            return_value=MagicMock(request=fake_get),
        ),
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
        patch("time.sleep"),
        patch.object(br, "_verify_openserp_match", side_effect=verify),
    ):
        entry = {"citation": {"title": "V Corps Operations in the ETO"}}
        br._search_openserp_archive(
            "V Corps Operations in the ETO",
            "http://x",
            grok_client=object(),
            entry=entry,
        )
    assert calls["n"] <= 5, f"verify budget exceeded: {calls['n']} calls"


def test_not_citation_relocates_to_narrative_notes(tmp_path):
    """A not_citation record (narrative footnote) is moved out of output/bibliography/ into
    output/narrative_notes/ — bib file removed, note written with text + provenance."""
    import json

    bib = tmp_path / "output" / "bibliography"
    bib.mkdir(parents=True)
    f = bib / "bib_deadbeef00000000.json"
    f.write_text(
        json.dumps(
            {
                "BibliographyID": "01NOTE",
                "title": "Unknown",
                "alt_title": "An American heavy tank did not reach the theater until 1945",
                "search_status": "not_citation",
                "mentions": [
                    {
                        "MentionID": "01M",
                        "book": "RoerDams",
                        "chapter": "chapter2a",
                        "reference_type": "footnote",
                        "reference_number": "21",
                        "verbatim_reference": "An American heavy tank did not reach the theater until 1945.",
                        "source_url": "https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/USA-E-Siegfried-2.html#fn21",
                    }
                ],
            }
        )
    )
    br._relocate_narrative_note(f, json.loads(f.read_text()))
    assert not f.exists()  # removed from bibliography
    out = tmp_path / "output" / "narrative_notes" / "bib_deadbeef00000000.json"
    assert out.exists()
    note = json.loads(out.read_text())
    assert note["text"].startswith("An American heavy tank")
    assert note["book"] == "RoerDams" and note["reference_number"] == "21"
    assert "ibiblio.org" in note["source_url"]
    assert note["origin"] == "footnote_narrative"


def test_relocate_narrative_moves_s3_object_to_archive(tmp_path, monkeypatch):
    """Relocation MOVES the stale S3 bibliography object to a recoverable relocated/ archive path
    (copy-then-delete), not a bare delete — so a mis-relocation can be reconstructed within the
    60-day lifecycle window.
    """
    import json
    from unittest.mock import MagicMock

    bib = tmp_path / "output" / "bibliography"
    bib.mkdir(parents=True)
    f = bib / "bib_cafebabe00000000.json"
    f.write_text(
        json.dumps(
            {
                "BibliographyID": "01N",
                "search_status": "not_citation",
                "mentions": [
                    {
                        "verbatim_reference": "A narrative aside.",
                        "book": "B",
                        "chapter": "c1",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    s3 = MagicMock()
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: s3)
    br._relocate_narrative_note(f, json.loads(f.read_text()))
    assert not f.exists()
    assert (
        tmp_path / "output" / "narrative_notes" / "bib_cafebabe00000000.json"
    ).exists()
    # MOVE: copy to relocated/ first, then delete the output/ source.
    s3.copy_object.assert_called_once_with(
        Bucket="test-bucket",
        CopySource={
            "Bucket": "test-bucket",
            "Key": "output/bibliography/bib_cafebabe00000000.json",
        },
        Key="relocated/bibliography/bib_cafebabe00000000.json",
    )
    s3.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key="output/bibliography/bib_cafebabe00000000.json"
    )


def test_relocate_narrative_aborts_if_note_write_unconfirmed(tmp_path, monkeypatch):
    """Verify-before-delete (HIGH#4): if the narrative_notes write cannot be confirmed, the
    bibliography source must NOT be removed and no S3 move is attempted."""
    import json
    from unittest.mock import MagicMock

    bib = tmp_path / "output" / "bibliography"
    bib.mkdir(parents=True)
    f = bib / "bib_feedface00000000.json"
    f.write_text(
        json.dumps(
            {
                "BibliographyID": "01N",
                "mentions": [{"verbatim_reference": "x", "book": "B", "chapter": "c"}],
            }
        )
    )
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    s3 = MagicMock()
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: s3)
    # Force the verify step to report failure.
    monkeypatch.setattr(br, "_narrative_note_written", lambda out: False)
    br._relocate_narrative_note(f, json.loads(f.read_text()))
    assert f.exists()  # source preserved — never deleted on unconfirmed write
    s3.copy_object.assert_not_called()
    s3.delete_object.assert_not_called()


def test_relocate_narrative_cleans_sibling_shards(tmp_path, monkeypatch):
    """Relocation removes sibling per-writer shards (bib_<hash>__<writer>.json) locally and archives
    them in S3, so the additive full-corpus reconcile cannot resurrect the zombie."""
    import json
    from unittest.mock import MagicMock

    bib = tmp_path / "output" / "bibliography"
    bib.mkdir(parents=True)
    f = bib / "bib_abcdef0000000000.json"
    f.write_text(
        json.dumps(
            {
                "BibliographyID": "01N",
                "mentions": [{"verbatim_reference": "x", "book": "B", "chapter": "c"}],
            }
        )
    )
    # Two sibling shards for THIS record's stem (bib_abcdef0000000000__<writer>.json).
    shard1 = bib / "bib_abcdef0000000000__writerA.json"
    shard2 = bib / "bib_abcdef0000000000__writerB.json"
    shard1.write_text("{}")
    shard2.write_text("{}")
    # An UNRELATED record's shard must NOT be collaterally removed (scoped glob).
    unrelated = bib / "bib_9999999999999999__writerA.json"
    unrelated.write_text("{}")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    s3 = MagicMock()
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: s3)
    br._relocate_narrative_note(f, json.loads(f.read_text()))
    assert (
        not shard1.exists() and not shard2.exists()
    )  # this record's shards removed locally
    assert unrelated.exists()  # unrelated record's shard preserved (scoped cleanup)
    # Each of this record's shards archived (moved) in S3 too.
    archived = {c.kwargs["Key"] for c in s3.copy_object.call_args_list}
    assert "relocated/bibliography/bib_abcdef0000000000__writerA.json" in archived
    assert "relocated/bibliography/bib_abcdef0000000000__writerB.json" in archived
    assert "relocated/bibliography/bib_9999999999999999__writerA.json" not in archived


def test_relocate_narrative_noop_s3_without_bucket(tmp_path, monkeypatch):
    """No S3_BUCKET (local mode) -> relocation still works, no S3 call attempted."""
    import json
    from unittest.mock import MagicMock

    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("DATA_BUCKET", raising=False)
    bib = tmp_path / "output" / "bibliography"
    bib.mkdir(parents=True)
    f = bib / "bib_00.json"
    f.write_text(
        json.dumps(
            {
                "BibliographyID": "01N",
                "mentions": [{"verbatim_reference": "x", "book": "B", "chapter": "c"}],
            }
        )
    )
    s3 = MagicMock()
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: s3)
    br._relocate_narrative_note(f, json.loads(f.read_text()))
    assert not f.exists()
    s3.delete_object.assert_not_called()
