"""Registry loader + wiring: selector, attempt recording, page preservation,
durable error persistence."""

import json
from pathlib import Path

from src.enrichment.award_registry import make_selector, sources_for
from src.enrichment.award_source_pages import persist_sourcing_errors, preserve_page
from src.enrichment.award_sources import AwardCitation, enrich_person_awards


class _FakeStorage:
    def __init__(self):
        self.saved = {}

    def exists(self, path):
        return path in self.saved

    def write_bytes(self, path, data):
        self.saved[path] = data


class _StubSource:
    def __init__(self, name, citations, error=None):
        self.name = name
        self._citations = citations
        self.last_error = error

    def lookup(self, person_name, award_hint=""):
        if isinstance(self._citations, Exception):
            raise self._citations
        return list(self._citations)


def test_registry_builds_us_source_and_skips_unimplemented():
    # USA has an implemented adapter -> at least one source.
    assert any("Hall of Valor" in s.name for s in sources_for("USA"))
    # GBR adapters not implemented yet -> none built (graceful).
    assert sources_for("GBR") == []
    # disabled country -> none.
    assert sources_for("Belgium") == []


def test_enrich_records_attempts_including_no_match():
    stub = _StubSource("StubSrc", [])  # returns nothing -> no_match
    person = {
        "name": "Test Person",
        "biographical_profile": {
            "nationality": "USA",
            "military_awards": [{"award": "Medal of Honor"}],
        },
    }
    enrich_person_awards(person, lambda code: [stub])
    award = person["biographical_profile"]["military_awards"][0]
    attempts = award.get("sourcing_attempts")
    assert attempts and attempts[0]["outcome"] == "no_match"
    assert attempts[0]["source"] == "StubSrc"


def test_enrich_records_error_with_url_from_source():
    stub = _StubSource(
        "StubSrc", RuntimeError("boom"), error="HTTP 403 [url=https://x/y]"
    )
    person = {
        "name": "Test Person",
        "biographical_profile": {
            "nationality": "USA",
            "military_awards": [{"award": "Medal of Honor"}],
        },
    }
    enrich_person_awards(person, lambda code: [stub])
    attempt = person["biographical_profile"]["military_awards"][0]["sourcing_attempts"][
        0
    ]
    assert attempt["outcome"] == "error"
    assert "url=" in attempt["error"]  # URL is captured in the error


def test_preserve_page_idempotent():
    st = _FakeStorage()
    p1 = preserve_page(st, "us_hall_of_valor", "https://x/a", b"<html>", "text/html")
    assert p1 and p1 in st.saved
    before = dict(st.saved)
    p2 = preserve_page(
        st, "us_hall_of_valor", "https://x/a", b"<html-changed>", "text/html"
    )
    assert p2 == p1 and st.saved == before  # not rewritten


def test_persist_sourcing_errors_writes_only_on_error():
    st = _FakeStorage()
    # no errors -> nothing written
    assert (
        persist_sourcing_errors(st, "01PID", "Name", "MoH", [{"outcome": "no_match"}])
        is None
    )
    assert not st.saved
    # error -> a durable record is written with needs_retry status
    path = persist_sourcing_errors(
        st,
        "01PID",
        "Name",
        "Medal of Honor",
        [{"outcome": "error", "error": "HTTP 403 [url=https://x]", "source": "S"}],
    )
    assert path and path in st.saved
    rec = json.loads(st.saved[path].decode("utf-8"))
    assert rec["status"] == "needs_retry"
    assert rec["errors"][0]["error"].startswith("HTTP 403")


def test_award_domains_includes_registry_and_valor_hosts():
    from src.enrichment.award_registry import award_domains, is_award_domain

    domains = award_domains()
    # Registry-derived hosts
    assert "valor.militarytimes.com" in domains
    assert "podvignaroda.ru" in domains
    # valor aliases the old OpenSERP path targeted
    assert "valor.defense.gov" in domains
    # is_award_domain tolerates scheme + www + paths
    assert is_award_domain("https://valor.militarytimes.com/recipient/recipient-1/")
    assert is_award_domain("http://www.podvignaroda.ru/?id=1")
    # a non-award site is NOT skipped
    assert not is_award_domain("https://en.wikipedia.org/wiki/Audie_Murphy")


def test_registry_structural_integrity():
    """Every registry source is a well-formed entry (guards against YAML indent bugs
    that merge entries, e.g. the offline block overriding a country entry)."""
    from src.enrichment.award_registry import load_registry

    reg = load_registry()
    assert len(reg) >= 20
    ids = [s.get("id") for s in reg]
    assert len(ids) == len(set(ids)), "duplicate/merged source ids"
    for s in reg:
        assert s.get("id"), f"source missing id: {s}"
        assert s.get("nationality"), f"{s.get('id')} missing nationality"
        assert s.get("adapter"), f"{s.get('id')} missing adapter"
    nats = {s["nationality"] for s in reg}
    assert "CSK" in nats, "Czechoslovakia entry lost (YAML merge regression)"
    assert "ANY" in nats, "generic offline fallback lost"
