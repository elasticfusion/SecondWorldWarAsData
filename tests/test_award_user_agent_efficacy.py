"""User-agent / header efficacy for award sources.

Two layers:
  * HERMETIC (always run): the per-source access profile from the registry builds the
    expected headers — the default full browser profile, plus any per-source
    user_agent / accept_language override ("optimized per search").
  * LIVE (opt-in, skipped in CI): actually fetch each ENABLED source's base URL with
    its configured headers and assert a usable (<400) response — the real efficacy
    check. Enable with AWARD_LIVE_UA_TEST=1 (makes polite network calls).
"""

import os

import pytest

from src.enrichment.award_registry import _access_profile, load_registry
from src.utils.http_pool import browser_headers


def test_default_profile_is_full_browser_header():
    h = browser_headers()
    assert h["User-Agent"].startswith("Mozilla/5.0")
    for required in ("Accept", "Accept-Language", "Sec-Fetch-Mode"):
        assert required in h, f"browser profile missing {required}"


def test_per_source_header_overrides_from_registry():
    """Each source's optimized profile applies its own UA / Accept-Language override
    on top of the shared browser headers."""
    by_id = {s["id"]: s for s in load_registry()}

    # Soviet source is tuned for Russian locale.
    podvig = by_id.get("su_podvig_naroda")
    if podvig:
        prof = _access_profile(podvig)
        hdr = browser_headers(prof["extra_headers"])
        assert hdr["Accept-Language"].startswith("ru")
        assert prof["crawl_delay"] >= 1

    # French source tuned for French locale.
    ordre = by_id.get("fr_ordre_liberation")
    if ordre:
        hdr = browser_headers(_access_profile(ordre)["extra_headers"])
        assert hdr["Accept-Language"].startswith("fr")

    # A source with no override still gets the default browser profile.
    hov = by_id.get("us_hall_of_valor")
    if hov:
        prof = _access_profile(hov)
        hdr = browser_headers(prof["extra_headers"])
        assert hdr["User-Agent"].startswith("Mozilla/5.0")


@pytest.mark.skipif(
    os.getenv("AWARD_LIVE_UA_TEST") != "1",
    reason="live UA-efficacy probe; set AWARD_LIVE_UA_TEST=1 to run (network)",
)
def test_live_user_agent_efficacy_per_enabled_source():
    """For each ENABLED online source, fetch its base URL with the configured headers
    and assert a usable response — validates the UA/header choice actually works."""
    import requests

    from src.utils.http_pool import get_session

    session = get_session()
    failures = []
    for s in load_registry():
        if not s.get("enabled") or s.get("adapter") == "offline":
            continue
        url = s["base_url"].rstrip("/") + s.get("search_path", "/").replace(
            "{q}", "test"
        )
        hdr = browser_headers(_access_profile(s)["extra_headers"])
        try:
            resp = session.get(url, headers=hdr, timeout=25, allow_redirects=True)
            if resp.status_code >= 400:
                failures.append(f"{s['id']}: HTTP {resp.status_code} [{url}]")
        except requests.RequestException as e:
            failures.append(f"{s['id']}: {type(e).__name__} [{url}]")
    assert not failures, "UA/header efficacy failures:\n" + "\n".join(failures)
