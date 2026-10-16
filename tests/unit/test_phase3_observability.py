"""Phase 3 observability: geocode skip-reason attribution (OBSERV geocode.dir)."""

import json

from src.enrichment.places_grok_geocode import geocode_places_dir


def _write(d, name, place):
    (d / f"{name}.json").write_text(json.dumps(place))


def test_geocode_skip_reasons_attributed(tmp_path):
    """Every skip is attributed to a reason (no black-box 'skipped N')."""
    pd = tmp_path / "places"
    pd.mkdir()
    # no usable name
    _write(pd, "noname", {"PlaceID": "01A", "coordinates": {}})
    # non-geocodable (unit-relative area)
    _write(
        pd,
        "unit",
        {"PlaceID": "01B", "current_name": "5th Division sector", "coordinates": {}},
    )
    # already has coords
    _write(
        pd,
        "hascoords",
        {
            "PlaceID": "01C",
            "current_name": "Aachen",
            "coordinates": {"latitude": 50.77, "longitude": 6.08},
        },
    )
    # already attempted (status set) + no coords + geocodable name
    _write(
        pd,
        "attempted",
        {
            "PlaceID": "01D",
            "current_name": "Düren",
            "coordinates": {},
            "enrichment_status": "geocoded",
        },
    )

    # No grok_client/geocoder needed: every place should SKIP before any resolve call.
    report = geocode_places_dir(pd, grok_client=None, write=False)

    assert report.attempted == 0
    assert report.skipped == 4
    assert report.skip_no_name == 1
    assert report.skip_non_geocodable == 1
    assert report.skip_has_coords == 1
    assert report.skip_already_attempted == 1
    # reason counters sum to the total (no unattributed skips)
    total_reasons = (
        report.skip_no_name
        + report.skip_non_geocodable
        + report.skip_has_coords
        + report.skip_already_attempted
    )
    assert total_reasons == report.skipped


def test_geocode_observability_never_raises(tmp_path):
    """Empty dir -> clean zero report, no exception (fail-safe OBSERV)."""
    pd = tmp_path / "places"
    pd.mkdir()
    report = geocode_places_dir(pd, grok_client=None, write=False)
    assert report.skipped == 0 and report.attempted == 0


def test_bib_resolve_captures_not_citation_samples(tmp_path, caplog):
    """resolve_bibliography_dir logs OBSERV bib.resolve with a sample of the rejected
    not-citation verbatims (so the military-citation pattern gap is visible)."""
    import logging
    from src.enrichment import bibliography_resolver as br

    bd = tmp_path / "bibliography"
    bd.mkdir()
    (bd / "r1.json").write_text(
        json.dumps(
            {
                "BibliographyID": "01X",
                "title": "Unknown",
                "mentions": [
                    {"verbatim_reference": "MS # B-810, Schmidt, Kaempfe im Rheinland."}
                ],
            }
        )
    )
    with caplog.at_level(logging.INFO):
        br.resolve_bibliography_dir(bd, grok_client=None, config={"holdings_index": {}})
    obs = [r.message for r in caplog.records if "OBSERV bib.resolve" in r.message]
    assert obs, "no OBSERV bib.resolve line emitted"
    assert "not_citation_samples=" in obs[0]
    assert "in=1" in obs[0]
    assert "not_citation_samples=[]" not in obs[0]  # the sample WAS captured
    assert "B-810" in obs[0]  # the actual rejected verbatim is visible


def test_bib_resolve_sample_scrubs_control_chars(tmp_path, caplog):
    """A crafted verbatim with \\r/\\n (untrusted-at-rest) must NOT splice a forged
    OBSERV line — control chars are scrubbed to spaces."""
    import logging

    from src.enrichment import bibliography_resolver as br

    bd = tmp_path / "bibliography"
    bd.mkdir()
    (bd / "r1.json").write_text(
        json.dumps(
            {
                "BibliographyID": "01Y",
                "title": "Unknown",
                "mentions": [
                    {"verbatim_reference": "evil\r\nOBSERV phase3.step name=forged"}
                ],
            }
        )
    )
    with caplog.at_level(logging.INFO):
        br.resolve_bibliography_dir(bd, grok_client=None, config={"holdings_index": {}})
    obs = [r.message for r in caplog.records if "OBSERV bib.resolve" in r.message]
    assert obs
    # the \r and \n inside the sample are replaced with spaces (no raw control chars)
    assert "\r" not in obs[0] and "\nOBSERV" not in obs[0]
    assert "evil  OBSERV phase3.step name=forged" in obs[0]  # \r\n -> two spaces
