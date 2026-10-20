"""SSRF-safe image/map download (Phase-2 downloads untrusted ibiblio image URLs).

fetch_image_bytes must: reject non-http(s) + loopback/private/metadata hosts BEFORE any
request; disable redirects (a redirect reopens SSRF); cap bytes; require image content; and
never raise on a fetch error (one bad URL must not abort extraction).
"""

from unittest import mock

import pytest

from src.extraction.images import fetch_image_bytes


def test_blocks_non_http_scheme():
    body, ext = fetch_image_bytes("file:///etc/passwd")
    assert body is None and ext is None


def test_blocks_loopback_and_metadata_hosts(monkeypatch):
    # clean_url rejects these at the host-string / IP-literal level — no request made.
    for url in (
        "http://127.0.0.1/x.jpg",
        "http://169.254.169.254/latest/meta-data/x.jpg",
        "http://localhost/x.jpg",
    ):
        body, _ = fetch_image_bytes(url)
        assert body is None, url


def test_blocks_host_resolving_to_private_ip(monkeypatch):
    # clean_url passes (public-looking hostname) but DNS resolves to a private IP -> blocked.
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: None
    )
    with mock.patch("src.extraction.images.requests.get") as g:
        body, _ = fetch_image_bytes("https://evil.example.org/map.jpg")
        assert body is None
        g.assert_not_called()  # never fetched


def test_successful_fetch_returns_bytes_and_disables_redirects(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    resp.headers = {"content-type": "image/jpeg"}
    resp.iter_content = lambda n: [b"\xff\xd8\xff" + b"x" * 2000]
    with mock.patch("src.extraction.images.requests.get", return_value=resp) as g:
        body, ext = fetch_image_bytes(
            "https://www.ibiblio.org/hyperwar/x/maps/USA-4.jpg"
        )
        assert body and ext == "jpg"
        # redirects MUST be disabled (a redirect reopens SSRF)
        assert g.call_args.kwargs.get("allow_redirects") is False


def test_fetch_error_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    with mock.patch("src.extraction.images.requests.get", side_effect=OSError("boom")):
        body, ext = fetch_image_bytes("https://www.ibiblio.org/x.jpg")
        assert body is None and ext is None  # swallowed, no raise


def test_download_writes_under_given_storage_dir(tmp_path, monkeypatch):
    from src.extraction.images import _download_image

    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes",
        lambda url: (b"\xff\xd8\xffdata", "jpg"),
    )
    storage = tmp_path / "output" / "images" / "files"
    path, ext = _download_image("https://ex.org/a.jpg", "01IMGID", storage)
    assert ext == "jpg"
    assert (storage / "01IMGID.jpg").exists()  # bytes written
    # local_copy is RELATIVE (portable), not an absolute /.../ path, and rooted at output/
    assert path == "images/files/01IMGID.jpg"
    assert not path.startswith("/")


def test_rejects_non_image_body_masquerade(monkeypatch):
    """A large text/html body must NOT be saved as an image (content-type gate + magic-byte)."""
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}
    resp.iter_content = lambda n: [b"<html>" + b"x" * 5000 + b"</html>"]
    with mock.patch("src.extraction.images.requests.get", return_value=resp):
        body, ext = fetch_image_bytes("https://ex.org/not-really.jpg")
        assert body is None and ext is None


def test_accepts_image_magic_bytes_with_odd_content_type(monkeypatch):
    """A real JPEG with a missing/odd content-type is still accepted (magic-byte fallback)."""
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/octet-stream"}
    resp.iter_content = lambda n: [b"\xff\xd8\xff" + b"\x00" * 3000]
    with mock.patch("src.extraction.images.requests.get", return_value=resp):
        body, ext = fetch_image_bytes("https://ex.org/x.jpg")
        assert body and ext == "jpg"
