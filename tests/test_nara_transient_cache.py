"""Regression (§1.0d radon-reduction review): _search_nara must distinguish a TRANSIENT NARA
outcome (429 / non-200 / HTML-shell / JSON-parse error) from a GENUINE not-found. A transient
outcome must NOT be negative-cached (so a retry can succeed next run); a genuine not-found MUST be
negative-cached. A helper-extraction refactor briefly regressed this by letting the transient
payload=None path fall through to the unconditional negative-cache write.
"""

from unittest.mock import MagicMock, patch

import src.enrichment.bibliography_resolver as br


class _Resp:
    def __init__(self, status, *, json_body=None, html=False):
        self.status_code = status
        self.headers = {"Content-Type": "text/html" if html else "application/json"}
        self._json = json_body if json_body is not None else {}
        self.text = "<!doctype html><html><div id='root'></div></html>" if html else ""
        self.content = self.text.encode()

    def json(self):
        return self._json


def _run_search(resp):
    """Drive _search_nara with a mocked session returning `resp`; return the list of
    cache_result calls (so we can assert whether a negative entry was written)."""
    cache_calls = []
    session = MagicMock()
    session.request.return_value = resp
    with (
        patch.object(br, "get_session", return_value=session),
        patch.object(br, "_nara_rate_limit", lambda: None),
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch(
            "src.utils.search_cache.cache_result",
            side_effect=lambda ns, q, v: cache_calls.append((ns, q, v)),
        ),
    ):
        result = br._search_nara("Some Title 1944", "key", MagicMock(), "verbatim")
    return result, cache_calls


def test_nara_non200_transient_not_negative_cached():
    result, cache_calls = _run_search(_Resp(503))
    assert result is None
    # A transient non-200 must NOT write a NOT_FOUND negative-cache entry.
    assert not any(
        v is None for (_, _, v) in cache_calls
    ), f"transient non-200 was negative-cached: {cache_calls}"


def test_nara_html_shell_transient_not_negative_cached():
    result, cache_calls = _run_search(_Resp(200, html=True))
    assert result is None
    assert not any(
        v is None for (_, _, v) in cache_calls
    ), f"HTML-shell invalid-query was negative-cached: {cache_calls}"


def test_nara_genuine_not_found_is_negative_cached():
    # 200 + valid JSON with zero hits = genuine not-found -> SHOULD negative-cache.
    body = {"body": {"hits": {"hits": [], "total": {"value": 0}}}}
    result, cache_calls = _run_search(_Resp(200, json_body=body))
    assert result is None
    assert any(
        v is None for (_, _, v) in cache_calls
    ), f"genuine not-found was NOT negative-cached: {cache_calls}"
