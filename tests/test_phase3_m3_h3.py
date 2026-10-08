"""Phase 3 M3 + H3 regression tests.

M3: a transient enrichment error must NOT be cached as a durable `not_found`
(which would suppress legitimate retries for the re-search window). Only a clean
negative stamps not_found/last_enrichment_search.

H3: the OpenSERP circuit breaker globals are lock-guarded + reset per run so an
opened breaker doesn't leak across books in one long-lived process.
"""

import json
from pathlib import Path

from src.grok_client import BatchModeCollecting  # noqa: F401 (import sanity)

# ---------------- M3: places ----------------


def test_place_transient_error_not_stamped_notfound(tmp_path, monkeypatch):
    import src.extraction.enrich_places as ep

    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"PlaceID": "01", "current_name": "Testville"}), "utf-8")

    # Grok raises a transient error; Wikipedia/Grokipedia helpers find nothing.
    monkeypatch.setattr(
        ep, "_try_grok", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503"))
    )
    monkeypatch.setattr(ep, "_fetch_place_wikipedia_full", lambda *a, **k: None)
    monkeypatch.setattr(ep, "_search_grokipedia_place", lambda *a, **k: None)

    changed = ep.enrich_place(pf, grok_client=object())
    assert changed is False
    saved = json.loads(pf.read_text())
    # The entity must NOT be stamped not_found — it should retry next run.
    assert saved.get("enrichment_status") != "not_found"
    assert "last_enrichment_search" not in saved


def test_place_clean_negative_is_stamped_notfound(tmp_path, monkeypatch):
    import src.extraction.enrich_places as ep

    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"PlaceID": "02", "current_name": "Nowhere"}), "utf-8")
    # No error anywhere, but nothing found -> clean negative.
    monkeypatch.setattr(ep, "_try_grok", lambda *a, **k: None)
    monkeypatch.setattr(ep, "_fetch_place_wikipedia_full", lambda *a, **k: None)
    monkeypatch.setattr(ep, "_search_grokipedia_place", lambda *a, **k: None)

    ep.enrich_place(pf, grok_client=object())
    saved = json.loads(pf.read_text())
    assert saved.get("enrichment_status") == "not_found"
    assert "last_enrichment_search" in saved


# ---------------- M3: groups ----------------


def test_group_transient_error_not_stamped_notfound(tmp_path, monkeypatch):
    import src.extraction.enrich_groups as eg

    class _ErrGrok:
        def extract_json(self, *a, **k):
            raise RuntimeError("timeout")

    gf = tmp_path / "g.json"
    gf.write_text(json.dumps({"GroupID": "01", "name": "Test Unit"}), "utf-8")
    eg.enrich_group(gf, _ErrGrok())
    saved = json.loads(gf.read_text())
    assert saved.get("enrichment_status") != "not_found"
    assert "last_enrichment_search" not in saved


# ---------------- H3: openserp breaker ----------------


def test_openserp_reset_circuit():
    import src.enrichment.openserp_enrichment as oe

    # Force the breaker open, then reset.
    for _ in range(oe._CIRCUIT_BREAKER_THRESHOLD):
        oe._breaker_record_failure()
    assert oe._breaker_is_open() is True
    oe.reset_circuit()
    assert oe._breaker_is_open() is False


def test_openserp_breaker_thread_safe():
    import threading

    import src.enrichment.openserp_enrichment as oe

    oe.reset_circuit()

    def hammer():
        for _ in range(100):
            oe._breaker_record_failure()
            oe._breaker_is_open()

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # No crash/race; breaker is open after many failures.
    assert oe._breaker_is_open() is True
    oe.reset_circuit()
    assert oe._breaker_is_open() is False
