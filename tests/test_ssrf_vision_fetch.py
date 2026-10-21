"""CRITICAL SSRF regression — grok_client.extract_json_with_image + _verify_single_image.

The vision image fetch takes an UNTRUSTED image_url (built from scraped page HTML in the Phase-3
map-verification path). It previously did a raw session.get(allow_redirects=True) with no host
validation — a crafted http://169.254.169.254/... or internal URL (or a redirect to one) was fetched
server-side. These tests assert the guard BEHAVIORALLY (bytes/call level), not by grepping source
(the original gap: the Pattern-2 grep test couldn't see a session.get in grok_client).
"""

from unittest import mock

import pytest


def _client():
    import src.grok_client as gc

    c = gc.GrokClient.__new__(gc.GrokClient)
    c.caches = {}
    c._cache_lock = __import__("threading").Lock()
    c._get_cache = lambda ct: c.caches.setdefault(ct, {})
    c._make_cache_key = lambda *a, **k: "ck"
    c._get_model = lambda ct: "m"
    return c


def test_extract_json_with_image_refuses_metadata_host():
    """A cloud-metadata / loopback / private image_url must be REFUSED before any vision API call."""
    import src.grok_client as gc

    c = _client()
    called = {"api": 0}
    c._call_api = lambda *a, **k: called.__setitem__("api", called["api"] + 1) or {}

    for url in (
        "http://169.254.169.254/latest/meta-data/iam/",
        "http://127.0.0.1/x.jpg",
        "http://10.0.0.5/internal.png",
        "http://2130706433/x.jpg",  # decimal 127.0.0.1
    ):
        with pytest.raises(gc.GrokAPIError):
            c.extract_json_with_image(prompt="p", image_url=url, use_cache=False)
    assert called["api"] == 0  # never reached the vision model


def test_extract_json_with_image_blocks_host_resolving_private(monkeypatch):
    """A public-looking host that DNS-resolves to a private IP is refused (rebinding defense)."""
    import src.grok_client as gc

    c = _client()
    c._call_api = lambda *a, **k: {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: None
    )
    with mock.patch("requests.get") as g:
        with pytest.raises(gc.GrokAPIError):
            c.extract_json_with_image(
                prompt="p", image_url="https://evil.example.org/m.jpg", use_cache=False
            )
        g.assert_not_called()  # never fetched


def test_extract_json_with_image_accepts_safe_image(monkeypatch):
    """A safe URL returning real image bytes goes through to the (mocked) vision call."""
    import src.grok_client as gc

    c = _client()
    c._call_api = lambda *a, **k: {"choices": [{"message": {"content": '{"ok": 1}'}}]}
    # Route the validated-bytes fetch to return a real JPEG without network.
    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes",
        lambda url: (b"\xff\xd8\xff" + b"x" * 2000, "jpg"),
    )
    out = c.extract_json_with_image(
        prompt="p", image_url="https://www.ibiblio.org/x/map.jpg", use_cache=False
    )
    assert out == {"ok": 1}


def test_verify_single_image_skips_unsafe_url():
    """The map-verification caller gates the scraped URL and skips (no grok call) on an unsafe host."""
    from src.extraction import search_external_maps as sem

    grok = mock.Mock()
    out = sem._verify_single_image(
        {"url": "http://169.254.169.254/map.png", "alt": "a", "caption": "c"},
        "Place",
        None,
        "ctx",
        "Map",
        grok,
        image_timeout=5,
    )
    assert out is None
    grok.extract_json_with_image.assert_not_called()
