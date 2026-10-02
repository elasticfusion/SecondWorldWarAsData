"""Hermetic tests for the Hall of Valor direct award-citation source.

No network: a fake session returns representative HTML (shaped like real
valor.militarytimes.com pages — search listing + recipient citation prose).
"""

from pathlib import Path

from src.enrichment.award_hall_of_valor import HallOfValorSource

_SEARCH_HTML = """
<html><body>
<a href="https://valor.militarytimes.com/recipient/recipient-209/">Audie Murphy</a>
<a href="https://valor.militarytimes.com/award/medal-of-honor/">Medal of Honor</a>
</body></html>
"""

_RECIPIENT_HTML = """
<html><head><title>Audie Murphy - Hall of Valor: Medal of Honor</title></head>
<body>
<p>The President of the United States of America, in the name of Congress, takes
pleasure in presenting the Medal of Honor to Second Lieutenant Audie Leon Murphy,
United States Army, for conspicuous gallantry and intrepidity in action above and
beyond the call of duty on 26 January 1945, at Holtzwihr, France.</p>
<p>The President of the United States of America takes pleasure in presenting the
Distinguished Service Cross to First Lieutenant Audie L. Murphy for extraordinary
heroism in connection with military operations against an armed enemy.</p>
</body></html>
"""


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if "?s=" in url:
            return _FakeResp(_SEARCH_HTML)
        if "/recipient/recipient-209/" in url:
            return _FakeResp(_RECIPIENT_HTML)
        return _FakeResp("", 404)


def test_hall_of_valor_parses_citations(tmp_path):
    src = HallOfValorSource(tmp_path / "cache", session=_FakeSession())
    res = src.lookup("Audie Murphy")
    awards = {c.award for c in res}
    assert "Medal of Honor" in awards
    assert "Distinguished Service Cross" in awards
    moh = [c for c in res if c.award == "Medal of Honor"][0]
    assert "conspicuous gallantry and intrepidity" in moh.citation_text
    assert moh.verified is True
    assert moh.source_url.endswith("/recipient/recipient-209/")
    assert moh.source_name == "Military Times Hall of Valor"
    assert moh.retrieved_date


def test_hall_of_valor_award_hint_filters(tmp_path):
    src = HallOfValorSource(tmp_path / "cache", session=_FakeSession())
    res = src.lookup("Audie Murphy", "DSC")  # alias -> Distinguished Service Cross
    assert res and all(c.award == "Distinguished Service Cross" for c in res)


def test_hall_of_valor_caches(tmp_path):
    sess = _FakeSession()
    src = HallOfValorSource(tmp_path / "cache", session=sess)
    src.lookup("Audie Murphy")
    n_first = len(sess.calls)
    src.lookup("Audie Murphy")  # served from disk cache
    assert len(sess.calls) == n_first  # no new network calls


def test_hall_of_valor_rejects_wrong_recipient(tmp_path):
    # Search returns the Murphy recipient page, but we query a different person →
    # name mismatch must reject (no fabricated attachment).
    src = HallOfValorSource(tmp_path / "cache", session=_FakeSession())
    res = src.lookup("Robert Smith")
    assert res == []


def test_canonical_award_strips_suffixes():
    from src.enrichment.award_hall_of_valor import _canonical_award

    assert _canonical_award("Medal of Honor (Posthumously)") == "Medal of Honor"
    assert _canonical_award("Silver Star (1st Oak Leaf Cluster)") == "Silver Star"
    assert (
        _canonical_award("Distinguished Service Cross") == "Distinguished Service Cross"
    )


def test_wwii_era_filter_excludes_non_wwii_citations():
    from src.enrichment.award_hall_of_valor import _is_wwii_citation

    # Spanish-American War (same-name recipient) -> excluded
    assert (
        _is_wwii_citation("heroism in action at Santiago, Cuba, 1 July 1898") is False
    )
    # Korea -> excluded
    assert _is_wwii_citation("for gallantry in action in Korea, 1951") is False
    # WWII -> included
    assert _is_wwii_citation("for gallantry on 6 June 1944 at Utah Beach") is True
    # undated prose -> not over-rejected
    assert _is_wwii_citation("for conspicuous gallantry in action") is True
