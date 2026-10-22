"""§1.0d HIGH regression: unit_patch._download must PIN the fetch to a vetted IP (close the
DNS-rebinding TOCTOU) and reject a host that resolves to a non-public IP. Previously it did a
plain re-resolving get_session().get(url), so a short-TTL attacker name could answer public on
_safe_url's earlier check and loopback/metadata at the _download connect.
"""

# pylint: disable=protected-access

from unittest.mock import MagicMock

from src.enrichment import unit_patch as up
from src.utils import url_hygiene as uh


def _fake_resp(body=b"IMGDATA", status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.iter_content = lambda _n=8192: [body]
    return resp


def test_download_pins_to_vetted_ip(monkeypatch):
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: ["93.184.216.34"])
    calls = {}

    def _fake_pinned(url, host, vetted, *, timeout, headers):
        calls.update(url=url, host=host, vetted=vetted, headers=headers)
        return _fake_resp()

    monkeypatch.setattr(uh, "_pinned_get", _fake_pinned)
    data = up._download("https://example.org/patch.png")
    assert data == b"IMGDATA"
    # Fetch was pinned to the vetted IP with Host = original host (not a re-resolving get).
    assert calls["host"] == "example.org"
    assert calls["vetted"] == ["93.184.216.34"]


def test_download_rejects_unsafe_host(monkeypatch):
    # Host resolves to a non-public IP (or rebind) -> resolved_host_is_safe returns None.
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: None)
    pinned_called = []
    monkeypatch.setattr(
        uh,
        "_pinned_get",
        lambda *a, **k: pinned_called.append(1) or _fake_resp(),
    )
    data = up._download("https://169.254.169.254/latest/meta-data/")
    assert data is None
    assert pinned_called == []  # never fetched


def test_download_non_200_returns_none(monkeypatch):
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(uh, "_pinned_get", lambda *a, **k: _fake_resp(status=404))
    assert up._download("https://example.org/missing.png") is None
