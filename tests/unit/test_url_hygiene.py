"""Clean-URL hygiene: validation, SSRF safety, option-2 query normalization."""

from src.utils.url_hygiene import clean_url, is_clean_url


def test_basic_normalize_lowercases_scheme_host_drops_fragment():
    assert clean_url("HTTPS://Example.COM/Path#frag") == "https://example.com/Path"


def test_default_port_dropped():
    assert clean_url("https://example.com:443/x") == "https://example.com/x"
    assert clean_url("http://example.com:80/x") == "http://example.com/x"


def test_option2_drops_tracking_keeps_identity_params():
    # tracking params dropped; identity params (id) kept
    assert (
        clean_url("https://ex.com/doc?utm_source=x&id=5&fbclid=abc")
        == "https://ex.com/doc?id=5"
    )
    # distinct identity values stay distinct
    assert clean_url("https://ex.com/doc?id=5") != clean_url("https://ex.com/doc?id=9")
    # NARA-style ?q= and viewer ?page= preserved
    assert "q=siegfried" in clean_url(
        "https://catalog.archives.gov/search?q=siegfried&ref=nav"
    )
    assert "page=12" in clean_url("https://viewer.example.com/v?page=12&session=xyz")


def test_pure_tracking_query_collapses():
    # a URL whose query is ONLY tracking collapses to the path (no query)
    assert clean_url("https://ex.com/a?utm_medium=email&gclid=z") == "https://ex.com/a"


def test_reject_non_http_schemes():
    for bad in (
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "ftp://x/y",
    ):
        assert clean_url(bad) is None


def test_reject_ssrf_hosts():
    for bad in (
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://[::1]/x",
    ):
        assert clean_url(bad) is None, bad


def test_reject_userinfo_and_malformed_and_overlong():
    assert clean_url("https://user:pass@example.com/x") is None
    assert clean_url("not a url") is None
    assert clean_url("") is None
    assert clean_url("https://example.com/" + "a" * 3000) is None


def test_public_host_allowed():
    assert is_clean_url("https://en.wikipedia.org/wiki/Battle_of_Aachen")
    assert is_clean_url("https://archive.org/details/some-book")


def test_reject_ssrf_alternate_ip_encodings():
    """BLOCKER fix: non-canonical IPv4 encodings that resolve to loopback/metadata
    must be rejected (decimal, hex, octal, short-form, trailing-dot)."""
    for bad in (
        "http://2130706433/x",  # decimal 127.0.0.1
        "http://0x7f000001/x",  # hex 127.0.0.1
        "http://0177.0.0.1/x",  # octal 127.0.0.1
        "http://127.1/x",  # short form 127.0.0.1
        "http://2852039166/x",  # decimal 169.254.169.254 (metadata)
        "http://169.254.169.254./x",  # trailing dot metadata
        "http://localhost./x",  # trailing dot localhost
    ):
        assert clean_url(bad) is None, bad


def test_query_identity_param_case_normalized_and_dedups():
    """Kept identity-param keys are lowercased so case variants dedup to one key."""
    assert clean_url("https://ex.com/d?ID=5") == clean_url("https://ex.com/d?id=5")


def test_path_params_jsessionid_dropped():
    """RFC-3986 path params (;jsessionid) are dropped so session tracking can't enter
    the dedup key."""
    assert clean_url("https://ex.com/a;jsessionid=123?id=5") == "https://ex.com/a?id=5"
