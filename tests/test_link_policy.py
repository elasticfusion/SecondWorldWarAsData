"""Task 1.9 (owner 2026-11-17): the trusted sources and the link-following limits are config
items read in one place (src/utils/link_policy), logged at the start of each run."""

import pytest

from src.utils import link_policy as lp

CFG = {
    "trusted_sources": [
        {
            "name": "hyperwar",
            "match": ["ibiblio.org/hyperwar/"],
            "forms": ["html", "pdf"],
        },
        {
            "name": "nara",
            "match": ["catalog.archives.gov/id/", "catalog.archives.gov/medialz/"],
        },
    ],
    "links": {"follow": {"same_domain": {"max_hops": 2, "max_per_page": 5}}},
}


@pytest.mark.parametrize(
    "url,basis",
    [("https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html", "hyperwar"),
     ("http://ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel.pdf", "hyperwar"),
     ("https://catalog.archives.gov/id/12345", "nara"),
     ("https://catalog.archives.gov/medialz/media/x.pdf", "nara"),
     ("https://www.ibiblio.org/pub/elsewhere.html", None),  # ibiblio.org is not all trusted
     ("https://catalog.archives.gov/search?q=x", None),
     ("https://ibiblio.org.evil.com/hyperwar/x.html", None),
     ("https://evil.com/ibiblio.org/hyperwar/x.html", None),
     ("ftp://ibiblio.org/hyperwar/x.html", None),
     ("not a url", None),
     # dot segments and escapes that a server resolves outside the trusted path
     ("https://ibiblio.org/hyperwar/../pub/x", None),
     ("https://ibiblio.org/hyperwar/%2e%2e/pub/x", None),
     ("https://ibiblio.org/hyperwar/%2E%2E%2Fpub", None),
     ("https://ibiblio.org/hyperwar/x;/../../pub", None),
     ("https://ibiblio.org/hyperwar/\t../x", None),
     ("https://catalog.archives.gov/id/1/../../search", None),
     # what clients read differently from urlparse
     ("https://evil.com\\@ibiblio.org/hyperwar/x", None),
     ("https://user@ibiblio.org/hyperwar/x", None),
     ("https://ibiblio.org:8080/hyperwar/x", None),
     ("https://ibiblio.org:evil/hyperwar/x", None),
     ("https://ibiblio.org./hyperwar/x", None),
     # other sites under the host, other paths, other case
     ("https://other.ibiblio.org/hyperwar/x", None),
     ("https://ibiblio.org/hyperwarez/x", None),
     ("https://ibiblio.org/HYPERWAR/x", None),
     ("https://ibiblio.org/hyperwar/%252e%252e/x", None),  # decoded twice by a proxy
     ("https://ibiblio.org/hyperwar/x%0a", None),
     ("https://ibiblio.org/hyperwar/%c0%ae%c0%ae/x", None),  # overlong UTF-8 dots
     ("https://ibiblio.org/hyperwar/USA/USA-E-XChannel%20Index.html", "hyperwar"),
     ("https://ibiblio.org:443/hyperwar/USA/x.html", "hyperwar")],
)  # fmt: skip
def test_trust_needs_the_host_and_the_path(url, basis):
    assert lp.trust_basis(url, CFG) == basis


@pytest.mark.parametrize(
    "match",
    ["https://full.url/docs/", "ibiblio.org", "ibiblio.org/", "ibiblio.org/hyperwar",
     "ibiblio.org/a/../b/", "ibiblio.org/%2e/", "evil.com\\x/docs/", "co/docs/", 7],
)  # fmt: skip
def test_a_malformed_entry_is_skipped_loudly(caplog, match):
    cfg = {"trusted_sources": [{"name": "x", "match": match},
                               {"name": "ok", "match": "ok.org/docs/"}]}  # fmt: skip
    assert [s.name for s in lp.trusted_sources(cfg)] == ["ok"]
    assert caplog.text.count("OBSERV link_policy.trusted_source_invalid") == 1


def test_an_unreadable_config_is_not_kept(monkeypatch):
    import src.utils.config as config

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("not yet")
        return CFG

    monkeypatch.setattr(config, "load_config", flaky)
    assert lp.trusted_sources() == ()
    assert [s.name for s in lp.trusted_sources()] == ["hyperwar", "nara"]
    lp.trusted_sources()
    assert len(calls) == 2  # kept after the first good read


def test_a_config_without_trusted_sources_says_so(monkeypatch, caplog):
    import src.utils.config as config

    monkeypatch.setattr(config, "load_config", lambda: {"paths": {}})
    assert lp.trusted_sources() == ()
    assert "OBSERV link_policy.not_configured" in caplog.text


def test_no_config_trusts_nothing():
    assert lp.trust_basis("https://www.ibiblio.org/hyperwar/x.html", {}) is None


@pytest.mark.parametrize(
    "url,parent,scope",
    [("https://www.ibiblio.org/a", "https://ibiblio.org/b", lp.SAME_DOMAIN),
     ("https://docs.ibiblio.org/a", "https://ibiblio.org/b", lp.SAME_DOMAIN),
     ("https://archive.org/a", "https://ibiblio.org/b", lp.EXTERNAL),
     ("https://ibiblio.org.evil.com/a", "https://ibiblio.org/b", lp.EXTERNAL)],
)  # fmt: skip
def test_link_scope(url, parent, scope):
    assert lp.link_scope(url, parent) == scope


def test_follow_limits_come_from_config_with_defaults_and_ceilings():
    assert lp.follow_limits(lp.SAME_DOMAIN, CFG) == {"max_hops": 2, "max_per_page": 5}
    assert lp.follow_limits(lp.EXTERNAL, CFG) == lp.DEFAULT_FOLLOW
    wild = {"links": {"follow": {"external": {"max_hops": 99, "max_per_page": "lots"}}}}
    assert lp.follow_limits(lp.EXTERNAL, wild) == lp.DEFAULT_FOLLOW


def test_an_invalid_follow_value_is_reported(caplog):
    lp.follow_limits(lp.EXTERNAL, {"links": {"follow": {"external": {"max_hops": 99}}}})
    assert (
        "OBSERV link_policy.follow_invalid scope=external key=max_hops" in caplog.text
    )


def test_the_policy_is_logged(caplog):
    lp.log_policy(CFG)
    assert (
        "OBSERV link_policy.loaded trusted='hyperwar:ibiblio.org/hyperwar/"
        in caplog.text
    )


def test_the_shipped_config_trusts_hyperwar_and_nara():
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((root / "config.yaml.example").read_text())
    assert lp.trust_basis("https://www.ibiblio.org/hyperwar/x.html", cfg) == "hyperwar"
    assert lp.trust_basis("https://catalog.archives.gov/id/1", cfg) == "nara"
    assert lp.follow_limits(lp.EXTERNAL, cfg) == {"max_hops": 1, "max_per_page": 20}
