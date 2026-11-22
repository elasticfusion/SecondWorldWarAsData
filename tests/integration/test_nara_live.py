"""Live check of the NARA catalog: a record with scans gives download URLs our code accepts, and
the first one downloads with the stated size and type. Catches NARA moving its media paths or
changing its reply shape before a real run does.

Not run by default (``requires_api``): one catalog search (quota: 10,000 a month) and one HEAD.

    set -a; . ./.env; set +a; .venv/bin/pytest -m requires_api tests/integration/test_nara_live.py
"""

import os

import pytest
import requests

from src.enrichment import acquisition_fetchers as af

pytestmark = pytest.mark.requires_api


def test_a_record_with_scans_gives_downloadable_urls_we_accept():
    key = os.environ.get("NARA_API_KEY", "")
    if not key:
        pytest.skip("NARA_API_KEY not set")
    resp = requests.get(
        "https://catalog.archives.gov/api/v2/records/search",
        params={
            "q": "World War II operations report",
            "availableOnline": "true",
            "levelOfDescription": "fileUnit",
            "limit": "5",
        },
        headers={"x-api-key": key, "Accept": "application/json"},
        timeout=30,
    )
    assert resp.status_code == 200, resp.status_code
    hits = (resp.json().get("body", {}).get("hits", {}) or {}).get("hits", [])
    records = [
        (h.get("_source") or {}).get("record") or {}
        for h in hits
        if ((h.get("_source") or {}).get("record") or {}).get("digitalObjects")
    ]
    assert (
        records
    ), "no record with scans in the reply: the search or the reply shape changed"
    scans = af.nara_scans(records[0])
    assert scans["scans"], f"every scan URL was refused (refused={scans['refused']}): " \
        "has NARA moved its media? see acquisition_fetchers.NARA_SCAN_PATHS"  # fmt: skip
    first = scans["scans"][0]
    head = requests.head(first["url"], timeout=30, allow_redirects=False)
    assert head.status_code == 200, head.status_code
    if first["size"]:
        assert int(head.headers.get("Content-Length", "0")) == first["size"]
    assert head.headers.get("Content-Type", "").split("/")[0] in (
        "image",
        "application",
    )
