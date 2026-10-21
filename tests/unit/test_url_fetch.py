"""Central URL fetch: go/no-go, SSRF-safe redirects + DNS-rebinding, Wayback fallback."""

from unittest.mock import MagicMock, patch

import src.utils.url_fetch as uf
import src.utils.url_registry as reg


class _Resp:
    def __init__(self, status=200, text="ok", location=None):
        self.status_code = status
        self._text = text
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None
        self.encoding = "utf-8"

    def iter_content(self, _n):
        yield self._text.encode("utf-8")

    def close(self):
        pass


def _go(monkeypatch, resolves_safe=True):
    """Registry says GO + claim succeeds; host resolves to a public IP (unless told
    otherwise, to exercise the DNS-rebinding block)."""
    monkeypatch.setattr(reg, "decide", lambda u: (reg.GO, None))
    monkeypatch.setattr(reg, "claim", lambda u: True)
    monkeypatch.setattr(
        uf,
        "resolved_host_is_safe",
        lambda h: (["93.184.216.34"] if resolves_safe else None),
    )


def _pin(monkeypatch, resp):
    """Pin the fetch seam: _one_hop now calls url_hygiene._pinned_get (IP-pinned). Return `resp`."""
    pg = MagicMock(return_value=resp)
    monkeypatch.setattr("src.utils.url_hygiene._pinned_get", pg)
    return pg


def test_live_200_used(monkeypatch):
    _go(monkeypatch)
    _pin(monkeypatch, _Resp(200, "<html>real content</html>"))
    with patch("src.enrichment.wayback.is_block_page", return_value=False):
        r = uf.fetch("https://en.wikipedia.org/wiki/X")
    assert r.source == uf.LIVE and "real content" in r.content
    assert r.final_url == "https://en.wikipedia.org/wiki/X"  # host lowered, path kept


def test_dns_rebinding_host_resolving_private_blocked(monkeypatch):
    """A host that passes clean_url (string) but RESOLVES to a private/metadata IP is
    blocked at fetch time (BLOCKER-1 DNS-rebinding)."""
    _go(monkeypatch, resolves_safe=False)
    pg = _pin(monkeypatch, _Resp(200, "secret internal page"))
    with patch("src.enrichment.wayback.fetch_archived", return_value=None):
        r = uf.fetch("https://rebind.example.com/a")
    assert r.source != uf.LIVE
    pg.assert_not_called()  # never even issued the GET


def test_ssrf_redirect_to_metadata_blocked(monkeypatch):
    _go(monkeypatch)
    _pin(
        monkeypatch, _Resp(302, "", location="http://169.254.169.254/latest/meta-data/")
    )
    with patch("src.enrichment.wayback.fetch_archived", return_value=None):
        r = uf.fetch("https://evil.example.com/a")
    assert r.source != uf.LIVE


def test_404_falls_out_to_wayback(monkeypatch):
    _go(monkeypatch)
    _pin(monkeypatch, _Resp(404, ""))
    with patch(
        "src.enrichment.wayback.fetch_archived",
        return_value=("<archived/>", "https://web.archive.org/x", "20200101000000"),
    ):
        r = uf.fetch("https://gone.example.com/a")
    assert r.source == uf.WAYBACK and r.wayback_ts == "20200101000000"


def test_block_page_falls_out_to_wayback(monkeypatch):
    _go(monkeypatch)
    _pin(monkeypatch, _Resp(200, "verifying you are human"))
    with (
        patch("src.enrichment.wayback.is_block_page", return_value=True),
        patch(
            "src.enrichment.wayback.fetch_archived",
            return_value=("<archived/>", "https://web.archive.org/y", "20190101000000"),
        ),
    ):
        r = uf.fetch("https://walled.example.com/a")
    assert r.source == uf.WAYBACK


def test_gone_no_archive_counts_toward_dead(monkeypatch):
    _go(monkeypatch)
    _pin(monkeypatch, _Resp(404, ""))
    calls = {"n": 0}
    monkeypatch.setattr(
        reg,
        "record_dns_failure",
        lambda u: calls.__setitem__("n", calls["n"] + 1) or "retry",
    )
    with patch("src.enrichment.wayback.fetch_archived", return_value=None):
        r = uf.fetch("https://gone.example.com/z")
    assert calls["n"] == 1 and r.source == uf.BLOCKED_NO_ARCHIVE  # only 'gone' counts


def test_rate_limited_never_counts_toward_dead(monkeypatch):
    """429/503 must NOT call record_dns_failure (never age toward dead)."""
    _go(monkeypatch)
    _pin(monkeypatch, _Resp(429, "slow down"))
    called = {"dead": 0}
    monkeypatch.setattr(
        reg, "record_dns_failure", lambda u: called.__setitem__("dead", 1) or "retry"
    )
    with patch("src.enrichment.wayback.fetch_archived", return_value=None):
        r = uf.fetch("https://busy.example.com/a")
    assert r.source == uf.RATE_LIMITED and called["dead"] == 0


def test_transient_never_counts_toward_dead(monkeypatch):
    """A transport error (transient) + no archive = IGNORE, never record_dns_failure."""
    _go(monkeypatch)
    monkeypatch.setattr(
        "src.utils.url_hygiene._pinned_get",
        MagicMock(side_effect=Exception("DNS timeout")),
    )
    called = {"dead": 0}
    monkeypatch.setattr(
        reg, "record_dns_failure", lambda u: called.__setitem__("dead", 1) or "retry"
    )
    with patch("src.enrichment.wayback.fetch_archived", return_value=None):
        r = uf.fetch("https://flaky.example.com/a")
    assert r.source == uf.BLOCKED_NO_ARCHIVE and called["dead"] == 0


def test_registry_defer_skips_fetch(monkeypatch):
    monkeypatch.setattr(reg, "decide", lambda u: (reg.DEFER_DOMAIN, None))
    called = {"claim": 0}
    monkeypatch.setattr(reg, "claim", lambda u: called.__setitem__("claim", 1) or True)
    r = uf.fetch("https://ex.com/a")
    assert r.source == uf.DEFERRED and called["claim"] == 0


def test_skip_known_returns_without_fetch(monkeypatch):
    monkeypatch.setattr(reg, "decide", lambda u: (reg.SKIP_KNOWN, "rec"))
    r = uf.fetch("https://ex.com/known")
    assert r.source == uf.SKIP and r.registry_record == "rec"


def test_non_clean_url_skip(monkeypatch):
    r = uf.fetch("http://169.254.169.254/latest/")
    assert r.source == uf.SKIP


def test_http_status_captured_live_and_gone(monkeypatch):
    _go(monkeypatch)
    _pin(monkeypatch, _Resp(200, "<html>x</html>"))
    with patch("src.enrichment.wayback.is_block_page", return_value=False):
        assert uf.fetch("https://ex.com/a").http_status == 200
    _pin(monkeypatch, _Resp(404, ""))
    monkeypatch.setattr(reg, "record_dns_failure", lambda u: "retry")
    with patch("src.enrichment.wayback.fetch_archived", return_value=None):
        assert uf.fetch("https://ex.com/b").http_status == 404
