"""F2 (OpenSERP URL lifecycle): fetch_image_bytes_with_reason returns a SPECIFIC failure reason so
vision.image_blocked can distinguish a real SSRF block from a benign not-an-image page URL (the old
single 'ssrf_or_invalid' conflated ssrf_blocked / not_an_image / fetch_error / too_large).
"""

import src.extraction.images as IM


def test_ssrf_blocked_reason(monkeypatch):
    monkeypatch.setattr("src.utils.url_hygiene.clean_url", lambda u: None)
    b, e, r = IM.fetch_image_bytes_with_reason("http://169.254.169.254/latest")
    assert (b, e, r) == (None, None, "ssrf_blocked")


def test_not_an_image_reason(monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-type": "text/html"}

        def iter_content(self, n):  # an HTML page body
            return [b"<html>" + b"x" * 2000 + b"</html>"]

    monkeypatch.setattr(
        IM, "_safe_image_response_with_reason", lambda c: (_Resp(), "ok")
    )
    monkeypatch.setattr("src.utils.url_hygiene.clean_url", lambda u: u)
    b, e, r = IM.fetch_image_bytes_with_reason(
        "https://en.wikipedia.org/wiki/28th_Infantry_Division"
    )
    assert (b, e, r) == (None, None, "not_an_image")


def test_fetch_image_bytes_still_two_tuple(monkeypatch):
    # The legacy (bytes, ext) contract is preserved for existing callers.
    monkeypatch.setattr(
        IM, "fetch_image_bytes_with_reason", lambda u: (b"img", "jpg", "ok")
    )
    out = IM.fetch_image_bytes("https://x/y.jpg")
    assert out == (b"img", "jpg") and len(out) == 2
