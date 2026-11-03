"""DRY P1.2 guard: the hand-assembled SSRF prelude (clean_url + resolved_host_is_safe) that four
untrusted-source fetchers each re-implemented is now ONE shared chokepoint,
url_hygiene.vet_url_for_fetch. These tests pin (a) the chokepoint's own contract and (b) that every
rewired caller routes through it — so a future SSRF-hardening change to vet_url_for_fetch reaches
ALL sites at once (the whole point of the consolidation).
"""

from unittest import mock

import src.utils.url_hygiene as uh


# --- (a) the shared chokepoint's contract ---------------------------------------------------
def test_vet_rejects_unsafe_scheme_or_host(monkeypatch):
    # clean_url gate: loopback/metadata/non-http -> None (no resolve attempted).
    for bad in (
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.org/x",
    ):
        assert uh.vet_url_for_fetch(bad, scope="test") is None


def test_vet_rejects_host_resolving_private(monkeypatch):
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: None)
    assert uh.vet_url_for_fetch("https://evil.example.org/x", scope="test") is None


def test_vet_returns_vetted_on_safe_host(monkeypatch):
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: ["93.184.216.34"])
    res = uh.vet_url_for_fetch("https://example.org/doc?id=5", scope="test")
    assert res is not None
    assert res.host == "example.org"
    assert res.vetted == ["93.184.216.34"]
    assert res.url == "https://example.org/doc?id=5"  # original preserved (query kept)


def test_vet_folds_params_into_query(monkeypatch):
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: ["1.2.3.4"])
    res = uh.vet_url_for_fetch(
        "https://archive.org/wayback/available",
        scope="wayback",
        params={"url": "https://x.com", "timestamp": "2020"},
    )
    assert res is not None
    assert "url=https%3A%2F%2Fx.com" in res.url and "timestamp=2020" in res.url


# --- (b) every rewired caller routes through the shared chokepoint --------------------------
# Spy on vet_url_for_fetch itself (return None = block) and assert each caller INVOKES it. This
# proves routing-through-the-chokepoint (a future edit that re-inlined a prelude would fail here),
# not merely that some block occurs. _pinned_get must never be reached on a block.
def test_source_retrieval_routes_through_chokepoint(monkeypatch):
    import src.enrichment.source_retrieval as sr

    spy = mock.Mock(return_value=None)
    monkeypatch.setattr(uh, "vet_url_for_fetch", spy)
    with mock.patch.object(uh, "_pinned_get") as pinned:
        out = sr._download_to_quarantine(
            {}, "https://evil.example.org/x", mock.MagicMock(), max_bytes=1000
        )
        assert out["status"] == "error" and "ssrf" in out["reason"].lower()
        assert spy.called
        pinned.assert_not_called()


def test_award_ssrf_ok_routes_through_chokepoint(monkeypatch):
    from src.enrichment.award_polite_source import PoliteSource

    spy = mock.Mock(return_value=None)
    monkeypatch.setattr(uh, "vet_url_for_fetch", spy)
    src = PoliteSource.__new__(PoliteSource)  # no __init__ network
    src.last_error = None
    assert src._ssrf_ok("https://evil.example.org/x") is None
    assert spy.called
    assert "ssrf" in (src.last_error or "").lower()


def test_wayback_vet_routes_through_chokepoint(monkeypatch):
    import src.enrichment.wayback as wb

    spy = mock.Mock(return_value=None)
    monkeypatch.setattr(uh, "vet_url_for_fetch", spy)
    assert wb._vet_url("https://evil.example.org/x", None) is None
    assert spy.called


def test_unit_patch_safe_url_routes_through_chokepoint(monkeypatch):
    import src.enrichment.unit_patch as up

    spy = mock.Mock(return_value=None)
    monkeypatch.setattr(uh, "vet_url_for_fetch", spy)
    assert up._safe_url("https://evil.example.org/x.jpg") is None
    assert spy.called
