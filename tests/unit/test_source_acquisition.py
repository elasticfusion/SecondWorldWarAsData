"""Source-acquisition decision gate + pluggable fetcher registry/plan."""

import pytest

from src.enrichment.source_acquisition import (
    AcquisitionTarget,
    acquisition_decision,
    plan_acquisition,
    register_fetcher,
    get_fetcher,
)


def test_review_gate_image_vs_text_thresholds():
    # image-based (PDF / NARA page sets): review at/above the LOW threshold (50)
    assert acquisition_decision("pdf", 1294, public_domain=True).verdict == "review"
    assert acquisition_decision("tif", 50, public_domain=True).verdict == "review"
    assert acquisition_decision("tif", 10, public_domain=True).verdict == "auto"
    # text formats: auto to a MUCH higher threshold
    assert acquisition_decision("epub", 500, public_domain=True).verdict == "auto"
    assert acquisition_decision("csv", 5, public_domain=True).verdict == "auto"
    assert acquisition_decision("txt", 5000, public_domain=True).verdict == "review"


def test_review_gate_licensing_and_unknown():
    # non-public-domain -> always review regardless of size/format
    assert acquisition_decision("txt", 1, public_domain=False).verdict == "review"
    assert acquisition_decision("pdf", 2, public_domain=False).verdict == "review"
    # unknown format -> conservative review
    assert acquisition_decision("xyz", 1, public_domain=True).verdict == "review"


def test_plan_acquisition_uses_registered_fetcher_and_lands_under_source():
    register_fetcher(
        "unit_test_src",
        lambda e, c: AcquisitionTarget(
            fmt="pdf", pages=3, object_urls=["https://x/a.pdf"], detail="t"
        ),
    )
    entry = {
        "BibliographyID": "01TESTID",
        "search_status": "resolved",
        "search_source": "unit_test_src",
        "copyright_status": {"status": "public_domain"},
        "resource_urls": [],
    }
    plan = plan_acquisition(entry, {})
    assert plan is not None
    assert plan.decision.verdict == "auto"  # small image -> auto
    assert plan.source_prefix == "source/unit_test_src/01TESTID/"
    assert plan.target.pages == 3


def test_plan_acquisition_none_when_unresolved_or_unsupported():
    assert plan_acquisition({"search_status": "not_found"}, {}) is None
    assert (
        plan_acquisition(
            {"search_status": "resolved", "search_source": "no_fetcher_src"}, {}
        )
        is None
    )


def test_fetchers_register_all_builtins():
    from src.enrichment.acquisition_fetchers import register_all

    register_all()
    for src in ("nara_catalog", "archive_org", "openserp", "openserp_archive"):
        assert get_fetcher(src) is not None


def test_openserp_fetcher_direct_document_only():
    from src.enrichment.acquisition_fetchers import _openserp_fetcher

    assert _openserp_fetcher({"resource_urls": ["https://x/landing.html"]}, {}) is None
    t = _openserp_fetcher({"resource_urls": ["https://x/report.pdf"]}, {})
    assert t is not None and t.fmt == "pdf"


def test_nara_fetcher_needs_naid_and_key():
    from src.enrichment.acquisition_fetchers import _nara_fetcher

    # no naId in resource_urls -> None
    assert (
        _nara_fetcher(
            {"resource_urls": ["https://archive.org/x"]}, {"nara_api_key": "k"}
        )
        is None
    )
    # naId present but no key -> None (can't look up objects)
    assert (
        _nara_fetcher({"resource_urls": ["https://catalog.archives.gov/id/123"]}, {})
        is None
    )
