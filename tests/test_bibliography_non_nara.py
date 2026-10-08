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
