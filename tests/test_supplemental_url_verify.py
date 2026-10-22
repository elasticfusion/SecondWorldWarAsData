"""§1.0d HIGH regression: supplemental enrichment must content-VERIFY a search-found URL against
the citation before storing it in resource_urls. sequential_search returns the FIRST hit per source
with no content match, so storing it unchecked silently attributes a wrong source. _enrich_material
now routes the hit through the bibliography verify gate (_verify_match); a non-matching hit is
dropped (OBSERV), a matching hit is stored, and no-client fails open (offline/test).
"""

import logging
from unittest.mock import patch

from src.extraction import supplemental as sup


def _material():
    return {
        "material_category": "referenced_material",
        "citation": {"title": "The Approach to France (1943)", "author": ["Smith"]},
    }


def test_verified_url_is_stored():
    mat = _material()
    with (
        patch(
            "src.extraction.supplemental_search.sequential_search",
            return_value=(
                "https://archive.org/details/approach-to-france",
                "archive_org",
            ),
        ),
        patch.object(sup, "_supplemental_url_verified", return_value=True),
    ):
        sup._enrich_material(mat, grok_client=object())
    assert mat.get("resource_urls") == [
        "https://archive.org/details/approach-to-france"
    ]
    assert mat.get("search_source") == "archive_org"


def test_unverified_url_is_dropped(caplog):
    mat = _material()
    with (
        patch(
            "src.extraction.supplemental_search.sequential_search",
            return_value=("https://archive.org/details/WRONG-book", "archive_org"),
        ),
        patch.object(sup, "_supplemental_url_verified", return_value=False),
    ):
        with caplog.at_level(logging.WARNING, logger="src.extraction.supplemental"):
            sup._enrich_material(mat, grok_client=object())
    # A non-matching hit must NOT be stored (silent wrong-source attribution prevented).
    assert not mat.get("resource_urls")
    assert "search_source" not in mat
    assert any("supplemental.url_rejected" in r.message for r in caplog.records)


def test_verify_gate_fail_open_without_client():
    """_supplemental_url_verified fails OPEN only when there is no grok_client (offline/test),
    matching _verify_match's own no-client behavior."""
    assert sup._supplemental_url_verified("Some Title", "https://x/y", None) is True


def test_verify_gate_delegates_to_bibliography_verify_match():
    """With a client, the gate delegates to bibliography_resolver._verify_match (the same gate the
    bibliography path uses)."""
    with patch(
        "src.enrichment.bibliography_resolver._verify_match", return_value=False
    ) as vm:
        out = sup._supplemental_url_verified("T", "https://x/y", object())
    assert out is False
    vm.assert_called_once()
