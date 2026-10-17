"""Test the broadened (non-NARA) bibliography OpenSERP archive search."""

from unittest.mock import MagicMock, patch

import src.enrichment.bibliography_resolver as br


def test_search_iterates_non_nara_repositories():
    issued = []

    def fake_get(u, params=None, timeout=None):
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
            return_value=MagicMock(get=fake_get),
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
